#!/bin/bash -l
source "$HOME/anaconda3/etc/profile.d/conda.sh"

AMP_SORTER="/home/parasitology/amplicon_sorter_2025-10-09.py"

# Check dependencies
for cmd in jq NanoPlot chopper python; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Required command not found: $cmd"
        exit 1
    fi
done

# Load config from JSON file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-${SCRIPT_DIR}/NBamplicon_config.json}"
if [ ! -f "$CONFIG" ]; then
    echo "Config file not found: $CONFIG"
    exit 1
fi
# Sample name – supports both a plain string and a JSON array
if jq -e '.SAMPLE | type == "array"' "$CONFIG" > /dev/null 2>&1; then
    mapfile -t samples < <(jq -r '.SAMPLE[]' "$CONFIG")
else
    mapfile -t samples < <(jq -r '[.SAMPLE] | .[]' "$CONFIG")
fi

# Input directory – supports both a plain string (shared) and a JSON array (one per sample)
if jq -e '.INPUTDIR | type == "array"' "$CONFIG" > /dev/null 2>&1; then
    mapfile -t inputdirs < <(jq -r '.INPUTDIR[]' "$CONFIG")
    if [[ ${#inputdirs[@]} -ne ${#samples[@]} ]]; then
        echo "ERROR: INPUTDIR array length (${#inputdirs[@]}) must match SAMPLE array length (${#samples[@]})." >&2
        exit 1
    fi
else
    single_inputdir="$(jq -r '.INPUTDIR' "$CONFIG")"
    inputdirs=()
    for _s in "${samples[@]}"; do inputdirs+=("$single_inputdir"); done
fi

# Barcode IDs – accepts either:
#   flat list          ["23","24","25"]       -> shared across all samples
#   list of lists      [["23"],["24","25"]]  -> one sub-list per sample
if jq -e '.barcodes[0] | type == "array"' "$CONFIG" > /dev/null 2>&1; then
    barcodes_per_sample=true
    n_barcode_sets="$(jq -r '.barcodes | length' "$CONFIG")"
    if [[ "$n_barcode_sets" -ne "${#samples[@]}" ]]; then
        echo "ERROR: barcodes array length ($n_barcode_sets) must match SAMPLE array length (${#samples[@]})." >&2
        exit 1
    fi
else
    barcodes_per_sample=false
    mapfile -t shared_barcodes < <(jq -r '.barcodes[]' "$CONFIG")
fi
OUTDIR="$(jq -r '.OUTDIR' "$CONFIG")"
# Minimum read quality threshold for Chopper to keep
CHOPPER_QUAL="$(jq -r '.CHOPPER_QUAL' "$CONFIG")"
AMPLICON_SORTER_ALLREADS="$(jq -r '.AMPLICON_SORTER_ALLREADS' "$CONFIG")"
THREADS="$(jq -r '.THREADS' "$CONFIG")"

# Validate shared required fields
for var in OUTDIR CHOPPER_QUAL; do
    if [ -z "${!var}" ] || [ "${!var}" = "null" ]; then
        echo "Missing required config field: $var"
        exit 1
    fi
done
if (( ${#samples[@]} == 0 )); then
    echo "Missing required config field: SAMPLE"
    exit 1
fi
if [[ "$barcodes_per_sample" == "false" ]] && (( ${#shared_barcodes[@]} == 0 )); then
    echo "ERROR: barcodes list is empty."
    exit 1
fi

# Helper: read a JSON field as a bash array, broadcasting a scalar/null to all samples
read_per_sample_array() {
    local field="$1" config="$2" count="$3"
    local -n _psa_out="$4"
    local type
    type="$(jq -r ".[\"${field}\"] | type" "$config")"
    if [ "$type" = "array" ]; then
        mapfile -t _psa_out < <(jq -r ".[\"${field}\"][] // \"null\"" "$config")
    else
        local val
        val="$(jq -r ".[\"${field}\"] // \"null\"" "$config")"
        _psa_out=()
        for (( _i=0; _i<count; _i++ )); do _psa_out+=("$val"); done
    fi
}

# Parse per-sample length parameters (scalar or array, one per sample)
n_samples=${#samples[@]}
read_per_sample_array FRAGMENT_SIZE           "$CONFIG" "$n_samples" frag_sizes
read_per_sample_array CHOPPER_LEN             "$CONFIG" "$n_samples" chopper_lens
read_per_sample_array AMPLICON_SORTER_MIN     "$CONFIG" "$n_samples" amp_mins
read_per_sample_array AMPLICON_SORTER_MAX     "$CONFIG" "$n_samples" amp_maxes
read_per_sample_array CHOPPER_LEN_PCT         "$CONFIG" "$n_samples" chopper_len_pcts
read_per_sample_array AMPLICON_SORTER_MIN_PCT "$CONFIG" "$n_samples" amp_min_pcts
read_per_sample_array AMPLICON_SORTER_MAX_PCT "$CONFIG" "$n_samples" amp_max_pcts

mkdir -p "$OUTDIR"

total_samples=${#samples[@]}
for sample_idx in "${!samples[@]}"; do
    SAMPLE="${samples[$sample_idx]}"
    INPUTDIR="${inputdirs[$sample_idx]}"
    sample_num=$((sample_idx + 1))

    echo ""
    echo "========================================"
    echo " Sample ${sample_num}/${total_samples}: ${SAMPLE}"
    echo "========================================"

    if [ -z "$INPUTDIR" ] || [ "$INPUTDIR" = "null" ]; then
        echo "ERROR: INPUTDIR is empty for sample '$SAMPLE'" >&2
        exit 1
    fi

    # Resolve per-sample length parameters, deriving from FRAGMENT_SIZE where null
    fs="${frag_sizes[$sample_idx]}"
    sample_chopper_len="${chopper_lens[$sample_idx]}"
    sample_amp_min="${amp_mins[$sample_idx]}"
    sample_amp_max="${amp_maxes[$sample_idx]}"
    sample_chopper_len_pct="${chopper_len_pcts[$sample_idx]}"
    sample_amp_min_pct="${amp_min_pcts[$sample_idx]}"
    sample_amp_max_pct="${amp_max_pcts[$sample_idx]}"

    # Apply default percentages if null/unset
    if [ "$sample_chopper_len_pct" = "null" ] || [ -z "$sample_chopper_len_pct" ]; then
        sample_chopper_len_pct=50
    fi
    if [ "$sample_amp_min_pct" = "null" ] || [ -z "$sample_amp_min_pct" ]; then
        sample_amp_min_pct=75
    fi
    if [ "$sample_amp_max_pct" = "null" ] || [ -z "$sample_amp_max_pct" ]; then
        sample_amp_max_pct=125
    fi

    if [ "$fs" != "null" ] && [ -n "$fs" ]; then
        echo "FRAGMENT_SIZE=${fs} bp  (pcts: CHOPPER_LEN=${sample_chopper_len_pct}%  AMP_MIN=${sample_amp_min_pct}%  AMP_MAX=${sample_amp_max_pct}%)"
        if [ "$sample_chopper_len" = "null" ] || [ -z "$sample_chopper_len" ]; then
            sample_chopper_len=$(awk "BEGIN{printf \"%d\", $fs * $sample_chopper_len_pct / 100 + 0.5}")
            echo "  Derived CHOPPER_LEN=${sample_chopper_len} (${sample_chopper_len_pct}% of FRAGMENT_SIZE)"
        fi
        if [ "$sample_amp_min" = "null" ] || [ -z "$sample_amp_min" ]; then
            sample_amp_min=$(awk "BEGIN{printf \"%d\", $fs * $sample_amp_min_pct / 100 + 0.5}")
            echo "  Derived AMPLICON_SORTER_MIN=${sample_amp_min} (${sample_amp_min_pct}% of FRAGMENT_SIZE)"
        fi
        if [ "$sample_amp_max" = "null" ] || [ -z "$sample_amp_max" ]; then
            sample_amp_max=$(awk "BEGIN{printf \"%d\", $fs * $sample_amp_max_pct / 100 + 0.5}")
            echo "  Derived AMPLICON_SORTER_MAX=${sample_amp_max} (${sample_amp_max_pct}% of FRAGMENT_SIZE)"
        fi
    fi
    for _vname in sample_chopper_len sample_amp_min sample_amp_max; do
        if [ -z "${!_vname}" ] || [ "${!_vname}" = "null" ]; then
            echo "ERROR: ${_vname} unresolved for sample '${SAMPLE}'. Set FRAGMENT_SIZE or the explicit parameter." >&2
            exit 1
        fi
    done
    echo "  CHOPPER_LEN=${sample_chopper_len}  AMP_MIN=${sample_amp_min}  AMP_MAX=${sample_amp_max}"

    # Resolve barcodes for this sample
    if [[ "$barcodes_per_sample" == "true" ]]; then
        mapfile -t barcodes < <(jq -r ".barcodes[${sample_idx}][]" "$CONFIG")
        if (( ${#barcodes[@]} == 0 )); then
            echo "ERROR: barcodes sub-list for sample '${SAMPLE}' (index ${sample_idx}) is empty." >&2
            exit 1
        fi
        echo "  Barcodes: ${barcodes[*]}"
    else
        barcodes=("${shared_barcodes[@]}")
    fi

    # Get barcode directory created by MinKnow
    first_dir="$(find "$INPUTDIR" -maxdepth 1 -mindepth 1 -type d | head -n1)"
    fastq_pass="${first_dir%/}/fastq_pass"
    echo "fastq_pass: $fastq_pass"

    for barcode in "${barcodes[@]}"; do
        barcodedir="${fastq_pass%/}/barcode${barcode}"
        mergedFQ="${OUTDIR}/${SAMPLE}_barcode${barcode}.fastq.gz"
        shopt -s nullglob
        files=( "$barcodedir"/*.fastq.gz )
        if (( ${#files[@]} )); then
            # merge split reads into one fastq file
            cat "${files[@]}" > "$mergedFQ"
        else
            echo "No fastq.gz files in: $barcodedir" >&2
            continue
        fi

        # Get read stats
        NanoPlot -t "$THREADS" \
            --fastq "${OUTDIR}/${SAMPLE}_barcode${barcode}.fastq.gz" \
            -o "${OUTDIR}/${SAMPLE}_barcode${barcode}_Nanoplot"

        # Perform QC to remove low quality reads
        # minimum length will depend on your amplicon sequence
        # ITS1 is roughly 280 - 300bp, so filter reads containing half that size
        gunzip -c "$mergedFQ" | chopper -q "$CHOPPER_QUAL" -l "$sample_chopper_len" |\
         gzip > "${OUTDIR}/${SAMPLE}_BC${barcode}_filtered.fastq.gz"
        if [ -f "${OUTDIR}/${SAMPLE}_BC${barcode}_filtered.fastq.gz" ]; then
            filteredFQ="${OUTDIR}/${SAMPLE}_BC${barcode}_filtered.fastq.gz"
        else
            echo "WARNING: Filtered FASTQ does not exist!"
            echo "Expected path: ${OUTDIR}/${SAMPLE}_BC${barcode}_filtered.fastq.gz"
        fi

        conda activate amplicon_sorter
        AMP_SORT_OUT="${OUTDIR}/${SAMPLE}_BC${barcode}_AMPSORT"
        ar_flag=""
        [[ "$AMPLICON_SORTER_ALLREADS" == "true" ]] && ar_flag="-ar"
        python "$AMP_SORTER" \
            -i "$filteredFQ" -min "$sample_amp_min" -max "$sample_amp_max" \
            $ar_flag -np "$THREADS" -o "$AMP_SORT_OUT"
        conda deactivate
    done
done