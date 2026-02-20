#!/usr/bin/env python3
"""
run_amplicon.py – CLI wrapper for NBamplicon.sh

Allows all config parameters to be set from the command line.
When multiple samples are supplied (--sample NB01 NB02 ...), each sample is
queued and processed sequentially using shared pipeline settings.

Usage examples
--------------
# Single sample, all settings explicit:
python run_amplicon.py \
    --inputdir /data/run1 \
    --sample NB01 \
    --barcodes 1 2 3 \
    --outdir /home/parasitology/NBAmpliconOutput \
    --chopper-qual 20 --chopper-len 150 \
    --amp-min 200 --amp-max 350 \
    --allreads --threads 16

# Multiple samples queued sequentially (shared settings, per-sample inputdir):
python run_amplicon.py \
    --inputdir /data/run1 /data/run2 /data/run3 \
    --sample NB01 NB02 NB03 \
    --barcodes 1 2 3 \
    --outdir /results/amplicon \
    --chopper-qual 20 --chopper-len 150 \
    --amp-min 200 --amp-max 350 \
    --threads 16

# Use existing JSON as base and override specific fields:
python run_amplicon.py \
    --config NBamplicon_config.json \
    --sample NB01 NB02 \
    --threads 32

# Preview configs without running:
python run_amplicon.py --config NBamplicon_config.json --sample CULE11 --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_amplicon.py",
        description="CLI wrapper for NBamplicon.sh with multi-sample queuing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Base config file (optional – values are overridden by explicit CLI flags)
    parser.add_argument(
        "--config", "-c",
        metavar="FILE",
        help="Path to a base NBamplicon_config.json. CLI flags override its values.",
    )

    # Pipeline parameters (all optional when --config is supplied)
    parser.add_argument(
        "--inputdir", "-i",
        nargs="+",
        metavar="DIR",
        help=(
            "MinKnow output directory. Provide one value (shared by all samples) "
            "or one per sample (must match --sample count)."
        ),
    )
    parser.add_argument(
        "--sample", "-s",
        nargs="+",
        metavar="NAME",
        help="Sample name(s). Multiple names queue jobs sequentially.",
    )
    parser.add_argument(
        "--barcodes", "-b",
        nargs="+",
        metavar="ID",
        help=(
            "Barcode ID(s) shared across all samples, e.g. --barcodes 23 24 25. "
            "For per-sample barcodes use nested lists in the JSON config: "
            '"barcodes": [["23","24"],["25","26"]]'
        ),
    )
    parser.add_argument(
        "--outdir", "-o",
        metavar="DIR",
        help="Output directory (shared across samples).",
    )
    parser.add_argument(
        "--chopper-qual", "-q",
        type=int,
        metavar="INT",
        dest="chopper_qual",
        help="Minimum read quality for Chopper (default from config).",
    )
    parser.add_argument(
        "--fragment-size", "-f",
        type=int,
        metavar="INT",
        dest="fragment_size",
        help=(
            "Expected PCR product size (bp). Automatically sets:\n"
            "  CHOPPER_LEN       = 50%% of FRAGMENT_SIZE\n"
            "  AMPLICON_SORTER_MIN = FRAGMENT_SIZE - 75%%\n"
            "  AMPLICON_SORTER_MAX = FRAGMENT_SIZE + 75%%\n"
            "Individual overrides (--chopper-len, --amp-min, --amp-max) take precedence."
        ),
    )
    parser.add_argument(
        "--chopper-len", "-l",
        type=int,
        metavar="INT",
        dest="chopper_len",
        help="Minimum read length for Chopper. Overrides FRAGMENT_SIZE derivation.",
    )
    parser.add_argument(
        "--amp-min",
        type=int,
        metavar="INT",
        dest="amp_min",
        help="Minimum amplicon length for amplicon_sorter. Overrides FRAGMENT_SIZE derivation.",
    )
    parser.add_argument(
        "--amp-max",
        type=int,
        metavar="INT",
        dest="amp_max",
        help="Maximum amplicon length for amplicon_sorter. Overrides FRAGMENT_SIZE derivation.",
    )
    parser.add_argument(
        "--chopper-len-pct",
        type=float,
        metavar="PCT",
        dest="chopper_len_pct",
        help="%% of FRAGMENT_SIZE used to derive CHOPPER_LEN (default: 50).",
    )
    parser.add_argument(
        "--amp-min-pct",
        type=float,
        metavar="PCT",
        dest="amp_min_pct",
        help="%% of FRAGMENT_SIZE used to derive AMPLICON_SORTER_MIN (default: 75).",
    )
    parser.add_argument(
        "--amp-max-pct",
        type=float,
        metavar="PCT",
        dest="amp_max_pct",
        help="%% of FRAGMENT_SIZE used to derive AMPLICON_SORTER_MAX (default: 125).",
    )
    parser.add_argument(
        "--allreads",
        action="store_true",
        default=None,
        help="Pass -ar (all-reads) flag to amplicon_sorter.",
    )
    parser.add_argument(
        "--no-allreads",
        action="store_false",
        dest="allreads",
        help="Do NOT pass -ar flag to amplicon_sorter.",
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        metavar="INT",
        help="Number of CPU threads.",
    )
    parser.add_argument(
        "--script",
        metavar="FILE",
        default=str(Path(__file__).parent / "NBamplicon.sh"),
        help="Path to NBamplicon.sh (default: NBamplicon.sh next to this script).",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        dest="dry_run",
        help="Print resolved configs and commands without executing them.",
    )
    parser.add_argument(
        "--keep-configs",
        action="store_true",
        dest="keep_configs",
        help="Keep per-sample temporary JSON config files after the run.",
    )

    return parser


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "INPUTDIR": [],
    "SAMPLE": [],
    "barcodes": [],
    "OUTDIR": None,
    "FRAGMENT_SIZE": None,
    "CHOPPER_QUAL": 20,
    "CHOPPER_LEN": None,
    "CHOPPER_LEN_PCT": None,
    "AMPLICON_SORTER_MIN": None,
    "AMPLICON_SORTER_MIN_PCT": None,
    "AMPLICON_SORTER_MAX": None,
    "AMPLICON_SORTER_MAX_PCT": None,
    "AMPLICON_SORTER_ALLREADS": True,
    "THREADS": 4,
}


def load_base_config(path: str) -> dict:
    with open(path) as fh:
        data = json.load(fh)
    # Normalise SAMPLE and INPUTDIR to lists for uniform handling
    if isinstance(data.get("SAMPLE"), str):
        data["SAMPLE"] = [data["SAMPLE"]]
    if isinstance(data.get("INPUTDIR"), str):
        data["INPUTDIR"] = [data["INPUTDIR"]]
    # Normalise barcodes: flat list -> wrap in outer list as "shared" marker
    # List of lists stays as-is for per-sample assignment
    b = data.get("barcodes")
    if isinstance(b, list) and len(b) > 0 and isinstance(b[0], str):
        data["barcodes"] = b   # flat list – kept flat; shell broadcasts
    return data


def derive_from_fragment_size(
    fragment_size: int,
    chopper_len_pct: float = 50,
    amp_min_pct: float = 75,
    amp_max_pct: float = 125,
) -> dict:
    """Compute the three length parameters from a fragment size (for display)."""
    return {
        "CHOPPER_LEN": round(fragment_size * chopper_len_pct / 100),
        "AMPLICON_SORTER_MIN": round(fragment_size * amp_min_pct / 100),
        "AMPLICON_SORTER_MAX": round(fragment_size * amp_max_pct / 100),
    }


def _resolve_array_field(
    cli_values: list[int] | None,
    base_value,
    n: int,
    field_name: str,
) -> list[int | None] | None:
    """
    Resolve a per-sample field to a list of length n.
    CLI values take priority over base config. A single value is broadcast.
    Returns None if nothing was specified (leave as null for shell derivation).
    """
    source = None
    if cli_values is not None:
        source = cli_values
    elif isinstance(base_value, list):
        source = base_value
    elif base_value is not None:
        source = [base_value]          # scalar → broadcast
    else:
        return None                    # unset → shell derives

    if len(source) == 1:
        return source * n
    elif len(source) == n:
        return source
    else:
        raise ValueError(
            f"{field_name} count ({len(source)}) must be 1 or match "
            f"sample count ({n})."
        )


def build_combined_config(
    base: dict,
    args: argparse.Namespace,
    samples: list[str],
    inputdirs: list[str],
) -> dict:
    """Merge base config + CLI overrides into a single config with array SAMPLE/INPUTDIR."""
    cfg = dict(base)
    n = len(samples)

    cfg["SAMPLE"] = samples
    cfg["INPUTDIR"] = inputdirs

    if args.barcodes is not None:
        cfg["barcodes"] = args.barcodes
    if args.outdir is not None:
        cfg["OUTDIR"] = args.outdir
    if args.chopper_qual is not None:
        cfg["CHOPPER_QUAL"] = args.chopper_qual
    if args.allreads is not None:
        cfg["AMPLICON_SORTER_ALLREADS"] = args.allreads
    if args.threads is not None:
        cfg["THREADS"] = args.threads

    # Resolve per-sample array fields (broadcast single value or store per-sample list)
    per_sample_fields = [
        (args.fragment_size,   "FRAGMENT_SIZE"),
        (args.chopper_len,     "CHOPPER_LEN"),
        (args.amp_min,         "AMPLICON_SORTER_MIN"),
        (args.amp_max,         "AMPLICON_SORTER_MAX"),
        (args.chopper_len_pct, "CHOPPER_LEN_PCT"),
        (args.amp_min_pct,     "AMPLICON_SORTER_MIN_PCT"),
        (args.amp_max_pct,     "AMPLICON_SORTER_MAX_PCT"),
    ]
    for cli_val, key in per_sample_fields:
        resolved = _resolve_array_field(cli_val, cfg.get(key), n, key)
        if resolved is not None:
            cfg[key] = resolved
        # else leave as-is (None/scalar → shell handles broadcast + derivation)

    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Return list of error strings; empty list means config is valid."""
    errors = []
    n = len(cfg.get("SAMPLE") or [])

    if not cfg.get("SAMPLE"):
        errors.append("  Missing required field: SAMPLE")
    if not cfg.get("INPUTDIR"):
        errors.append("  Missing required field: INPUTDIR")
    elif len(cfg["INPUTDIR"]) not in (1, n):
        errors.append(
            f"  INPUTDIR length ({len(cfg['INPUTDIR'])}) must be 1 or match "
            f"SAMPLE length ({n})"
        )
    for key in ["OUTDIR", "CHOPPER_QUAL", "THREADS"]:
        if cfg.get(key) is None:
            errors.append(f"  Missing required field: {key}")
    if not cfg.get("barcodes"):
        errors.append("  'barcodes' list is empty or missing.")
    else:
        b = cfg["barcodes"]
        # List of lists: validate length matches sample count
        if isinstance(b[0], list):
            if len(b) != n:
                errors.append(
                    f"  barcodes list length ({len(b)}) must match "
                    f"SAMPLE length ({n})"
                )
            else:
                for i, sub in enumerate(b):
                    sample = cfg["SAMPLE"][i] if i < n else f"index {i}"
                    if not sub:
                        errors.append(f"  [{sample}] barcodes sub-list is empty")
        # Flat list: OK (shared across all samples)

    # Validate per-sample array lengths
    for key in [
        "FRAGMENT_SIZE", "CHOPPER_LEN", "AMPLICON_SORTER_MIN", "AMPLICON_SORTER_MAX",
        "CHOPPER_LEN_PCT", "AMPLICON_SORTER_MIN_PCT", "AMPLICON_SORTER_MAX_PCT",
    ]:
        val = cfg.get(key)
        if isinstance(val, list) and len(val) not in (1, n):
            errors.append(
                f"  {key} length ({len(val)}) must be 1 or match SAMPLE length ({n})"
            )

    # Check every sample can resolve its length params
    def _sample_val(field_val, idx):
        if isinstance(field_val, list):
            return field_val[idx] if len(field_val) > 1 else field_val[0]
        return field_val

    for i in range(n):
        sample = cfg["SAMPLE"][i]
        fs = _sample_val(cfg.get("FRAGMENT_SIZE"), i)
        for key in ["CHOPPER_LEN", "AMPLICON_SORTER_MIN", "AMPLICON_SORTER_MAX"]:
            pv = _sample_val(cfg.get(key), i)
            if pv is None and fs is None:
                errors.append(
                    f"  [{sample}] {key} is null and no FRAGMENT_SIZE provided for derivation"
                )

    return errors


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def run_job(script: str, config_path: str, dry_run: bool) -> int:
    """
    Run NBamplicon.sh with the given config.
    Returns subprocess exit code (0 = success). In dry-run mode returns 0.
    """
    cmd = ["bash", script, config_path]
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Running: {' '.join(cmd)}")
    if dry_run:
        return 0
    result = subprocess.run(cmd)
    return result.returncode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # ---- Load base config ------------------------------------------------
    base_config = dict(DEFAULT_CONFIG)
    if args.config:
        if not os.path.isfile(args.config):
            parser.error(f"Config file not found: {args.config}")
        base_config.update(load_base_config(args.config))
        print(f"Loaded base config: {args.config}")

    # ---- Resolve samples --------------------------------------------------
    if args.sample:
        samples = args.sample
    elif base_config.get("SAMPLE"):
        samples = base_config["SAMPLE"] if isinstance(base_config["SAMPLE"], list) else [base_config["SAMPLE"]]
    else:
        parser.error("No sample(s) specified. Use --sample or provide a config with SAMPLE set.")

    # ---- Resolve inputdirs (broadcast single value or require 1-to-1 match) ----
    cli_inputdirs = args.inputdir
    if cli_inputdirs is not None:
        if len(cli_inputdirs) == 1:
            inputdirs = cli_inputdirs * len(samples)       # broadcast
        elif len(cli_inputdirs) == len(samples):
            inputdirs = cli_inputdirs
        else:
            parser.error(
                f"--inputdir count ({len(cli_inputdirs)}) must be 1 or match "
                f"--sample count ({len(samples)})."
            )
    elif base_config.get("INPUTDIR"):
        base_dirs = base_config["INPUTDIR"] if isinstance(base_config["INPUTDIR"], list) else [base_config["INPUTDIR"]]
        if len(base_dirs) == 1:
            inputdirs = base_dirs * len(samples)
        elif len(base_dirs) == len(samples):
            inputdirs = base_dirs
        else:
            parser.error(
                f"Config INPUTDIR length ({len(base_dirs)}) must be 1 or match "
                f"sample count ({len(samples)})."
            )
    else:
        parser.error("No INPUTDIR specified. Use --inputdir or provide a config with INPUTDIR set.")

    # ---- Validate script exists ------------------------------------------
    if not os.path.isfile(args.script):
        parser.error(f"Pipeline script not found: {args.script}")

    # ---- Build combined config and validate ------------------------------
    cfg = build_combined_config(base_config, args, samples, inputdirs)
    errors = validate_config(cfg)
    if errors:
        print("ERROR: Config is invalid:")
        for e in errors:
            print(e)
        sys.exit(1)

    # ---- Print queue summary ---------------------------------------------
    total = len(samples)
    print(f"\n{'='*60}")
    print(f"  NBamplicon job queue  ({total} sample{'s' if total > 1 else ''})")
    print(f"{'='*60}")

    def _display_val(field_val, idx):
        if isinstance(field_val, list):
            return field_val[idx] if len(field_val) > 1 else field_val[0]
        return field_val

    for idx, (sample, indir) in enumerate(zip(samples, inputdirs), 1):
        fs  = _display_val(cfg.get("FRAGMENT_SIZE"), idx - 1)
        cl  = _display_val(cfg.get("CHOPPER_LEN"), idx - 1)
        amin = _display_val(cfg.get("AMPLICON_SORTER_MIN"), idx - 1)
        amax = _display_val(cfg.get("AMPLICON_SORTER_MAX"), idx - 1)
        cl_pct   = _display_val(cfg.get("CHOPPER_LEN_PCT"),         idx - 1) or 50
        amin_pct = _display_val(cfg.get("AMPLICON_SORTER_MIN_PCT"), idx - 1) or 75
        amax_pct = _display_val(cfg.get("AMPLICON_SORTER_MAX_PCT"), idx - 1) or 125
        # Compute derived values for display when null
        if fs is not None:
            d = derive_from_fragment_size(fs, cl_pct, amin_pct, amax_pct)
            cl   = cl   if cl   is not None else d["CHOPPER_LEN"]
            amin = amin if amin is not None else d["AMPLICON_SORTER_MIN"]
            amax = amax if amax is not None else d["AMPLICON_SORTER_MAX"]
        frag_str = f"FRAG={fs} bp  (pcts: CHOPPER_LEN={cl_pct}%  AMP_MIN={amin_pct}%  AMP_MAX={amax_pct}%)" if fs is not None else "FRAG=?"
        # Per-sample or shared barcodes
        b = cfg["barcodes"]
        if isinstance(b[0], list):
            sample_barcodes = b[idx - 1]
        else:
            sample_barcodes = b
        print(
            f"  [{idx}/{total}] {sample}"
            f"  |  inputdir: {indir}"
            f"  |  barcodes: {sample_barcodes}"
            f"  |  outdir: {cfg['OUTDIR']}"
            f"\n          {frag_str}  CHOPPER_LEN={cl}  AMP_MIN={amin}  AMP_MAX={amax}"
        )
    print(f"{'='*60}\n")

    if args.dry_run:
        print("--- Combined config ---")
        print(json.dumps(cfg, indent=4))
        print()

    # ---- Write config and run -------------------------------------------
    tmp_path: str | None = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix="_combined.json",
            prefix="NBamplicon_",
            delete=False,
        )
        json.dump(cfg, tmp, indent=4)
        tmp.close()
        tmp_path = tmp.name

        config_display = tmp_path if (args.keep_configs or args.dry_run) else f"<temp:{tmp_path}>"
        print(f"Config written to: {config_display}")

        rc = run_job(args.script, tmp_path, args.dry_run)

    finally:
        if tmp_path and not args.keep_configs:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    status = "succeeded" if rc == 0 else f"FAILED (exit {rc})"
    print(f"\n{'='*60}")
    print(f"  Run {status}")
    print(f"{'='*60}")

    sys.exit(rc)


if __name__ == "__main__":
    main()
