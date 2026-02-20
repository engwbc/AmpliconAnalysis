INSTRUCTIONS

Before you start, ensure that the following files are located in the same directory.
This should be /home/parasitology/Desktop/amplicon_analysis

1. NBamplicon.sh
2. NBamplicon_config.json
3. run_amplicon.py

ANALYSIS SET UP

1. Open a text editor NBamplicon_config.json.
    
    You will find the file has the following fields:
{
    "INPUTDIR": [
        "/home/parasitology/Nanopore/EXPERIMENT_NAME/SAMPLE_FOLDER"
    ],
    "SAMPLE": ["SAMPLE1","SAMPLE2"],
    "barcodes": [["1","2","3"],["4","5"]],
    "OUTDIR": "/media/parasitology/HDD01/NB_AmpOutput",
    "FRAGMENT_SIZE": [1000,300],
    "CHOPPER_QUAL": 20,
    "CHOPPER_LEN": null,
    "CHOPPER_LEN_PCT": null,
    "AMPLICON_SORTER_MIN": null,
    "AMPLICON_SORTER_MIN_PCT": null,
    "AMPLICON_SORTER_MAX": null,
    "AMPLICON_SORTER_MAX_PCT": null,
    "AMPLICON_SORTER_ALLREADS": true,
    "THREADS": 16
}

2. The main settings to change are INPUTDIR, SAMPLE, barcodes, OUTDIR, and FRAGMENT_SIZE
    
    a. Setting `SAMPLE` and `barcodes`:
    
    If there are multiple samples, make sure that each sample name is quoted and comma-separated as shown above, otherwise if there is only
    one sample type in the sample name in quotation marks and remove any commas (,)

    If each sample contains multiple barcodes, then enclose the barcode ID in square brackets with quotation marks as shown above, 
    this will create a list ["1","2","3"]. How this works is that the IDs are grouped by their order in the list. E.g., ["1","2","3"] will be
    read by the program as SAMPLE1 barcodes, while ["4", "5"] will be barcodes for SAMPLE2

    IMPORTANT: Make sure not to remove the outer square brackets! 
    [["1"]] , e.g. one sample and one barcode

    b. Setting `FRAGMENT_SIZE`

    Set the expected PCR product fragment here. 
    If there are multiple samples, make sure each value is comma-separated as shown above.

    c. Setting `OUTDIR`

    Set the output folder - this is where NanoPlot, filtered FASTQ and amplicon_sorter FASTA will be saved to.

    d. `CHOPPER_QUAL` / `CHOPPER_LEN` / `CHOPPER_LEN_PCT`
    By default, the program sets Chopper to remove reads with QSCORE<20 and length < 50% of FRAGMENT_SIZE.
    If this is too stringent/lenient, then you can directly adjust CHOPPER_LEN or CHOPPER_LEN_PCT to the required value(s).
    A single value can be set which will be used across all samples, e.g., "CHOPPER_LEN_PCT" : 50
    For multiple samples this can be set to, for example: "CHOPPER_LEN_PCT" : [60,75,95]

    e. `AMPLICON_SORTER_MIN` / `AMPLICON_SORTER_MAX`
    By default, this sets a lower and upper limit of +-25% of `FRAGMENT_SIZE` for amplicon_sorter to keep.
    To change this, set a value in AMPLICON_SORTER_MIN_PCT and AMPLICON_SORTER_MAX_PCT:
    e.g., "AMPLICON_SORTER_MIN_PCT" : [50,75] - SAMPLE1 will use 50% and SAMPLE2 will use 75%.
    
    f. `THREADS`
    The amount of CPU cores - used to run processes in parallel where possible.
    Higher is normally faster, but the limit for our system is 24.
    It is NOT advised to use all 24 cores if other tasks are running in the background!
    Default is set to 16.

3. Run the pipeline with the following command: 
python run_amplicon.py --config NBamplicon_config.json

TROUBLESHOOTING

If the terminal says it cannot find any of the files, run the following command:
cd /home/parasitology/Desktop/amplicon_analysis

Alternatively, you can call the script as follows:

python $HOME/Desktop/amplicon_analysis/run_amplicon.py --config $HOME/Desktop/amplicon_analysis/NBamplicon_config.json

You can also create your own copy of the config file - just make sure to set it to the right one!

E.g., python $HOME/Desktop/amplicon_analysis/run_amplicon.py --config /path/to/new/config.json