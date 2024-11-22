#!/usr/bin/env bash

ORIG_DATA_DIR="$HOME/Box/CoganLab"
# OUTPUT_DIR="$ORIG_DATA_DIR/BIDS-1.1_GlobalLocal"
OUTPUT_DIR="$HOME/Workspace/Lexical"
TASKS=("LexicalDecRepDelay")
SUB_IDS=(D66)

#(D3 D5 D6 D7 D8 D9 D12 D14 D15 D16 D17 D18 D20 D22 D23 D24 D26 D27 D28 D29 D30 D31 D32 D53
# D57 D59 D60 D61 D65 D66 D69 D70 D71 D72 D73)
#D3 D5 D6 D7 D8 D9 D18 D20 D22 D23 D24 D26 D27 D28 D29 D30 D31 D32 D53
 #D57 D59 D60 D61 D63 D65 D66 D69 D70 D71 D72)

#declare -l mylist[30]

#BIDS_DIR="$OUTPUT_DIR/$TASK/BIDS"
for TASK in "${TASKS[@]}"
 do
    #uncomment mapfile line to run for all subjects
    #mapfile -t SUB_IDS < <(find "$ORIG_DATA_DIR/D_Data/$TASK" -maxdepth 1 -type d -name "D*" -exec basename {} \;) # This line taks all the subjects
    # OUTPUT_DIR="$ORIG_DATA_DIR/$TASK"
    BIDS_DIR="$OUTPUT_DIR/BIDS"
    ZIP=false

    if [ -d $OUTPUT_DIR ]
     then
        rm -rf $OUTPUT_DIR
    fi
    mkdir $OUTPUT_DIR

    if [ -d $BIDS_DIR ]
     then
        rm -rf $BIDS_DIR
    fi
    # mkdir -p $BIDS_DIR
    mkdir -p "$OUTPUT_DIR/stimuli"
    # shellcheck disable=SC2038
    find "$ORIG_DATA_DIR/task_stimuli" -iname "LexicalDecRepDelay" -type d -exec echo "{}/." \; | xargs -I{} cp -afv {} "$OUTPUT_DIR/stimuli/"
    TASKLOWER=$(echo $TASK | tr '[:upper:]' '[:lower:]')
    #echo "$ORIG_DATA_DIR/task_stimuli/$TASKLOWER/."
    #cp -av "$ORIG_DATA_DIR/task_stimuli/$TASKLOWER/." "$BIDS_DIR/stimuli/"

    for SUB_ID in "${SUB_IDS[@]}"
    do 
        #bring wanted files to work space
        #IDSPLIT=( $(grep -Eo '[^[:digit:]]+|[[:digit:]]+' <<<"SUB_ID") )
        #NEW_ID="${IDSPLIT[0]}$(printf %04d ${IDSPLIT[1]})"

        # Echo the SUB_ID to check if it's working
        echo "Processing subject ID: $SUB_ID"

        if [ -d "$OUTPUT_DIR/$SUB_ID" ]
        then
            # shellcheck disable=SC2115
            rm -rf "$OUTPUT_DIR/$SUB_ID"
        fi
        mkdir -p "$OUTPUT_DIR/$SUB_ID"
#
        #CT scan .nii
        find "$ORIG_DATA_DIR/ECoG_Recon_Full/$SUB_ID/elec_recon" -name "postimpRaw.nii.gz" -type f -exec cp -v {} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_CT.nii.gz" \;
        find "$ORIG_DATA_DIR/ECoG_Recon_Full/$SUB_ID/elec_recon" -regex ".*\($SUB_ID.*CT.*\)\|\(postimpRaw\)\.nii" -type f -exec cp -v {} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_CT.nii" \;
        #electrode locations .txt
#        find "$(dirname $ORIG_DATA_DIR)/ECoG_Recon/$SUB_ID/elec_recon" -name "${SUB_ID}_elec_locations_RAS.txt" -type f -exec cp -v {} "$OUTPUT_DIR/$SUB_ID/" \;
        find "$(dirname $ORIG_DATA_DIR)/ECoG_Recon/$SUB_ID/elec_recon" -regex ".*${SUB_ID}_elec_locations_RAS_.*\.txt" -type f -exec cp -v {} "$OUTPUT_DIR/$SUB_ID/" \;
        #T1 MRI file .mgz
        find "$ORIG_DATA_DIR/ECoG_Recon_Full/$SUB_ID/elec_recon" -name "T1.nii.gz" -type f -exec cp -vn {} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_T1w.nii.gz" \; | head -1
        find "$ORIG_DATA_DIR/ECoG_Recon_Full/$SUB_ID/elec_recon" -name "T1.nii.gz" -type f -exec rsync -P {} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_T1w.nii.gz" \; | head -1
        #stim file corrections
        if [ $TASK == "Phoneme_Sequencing" ]
        then
            cp -vn "$ORIG_DATA_DIR/ECoG_Task_Data/response_coding/PhonemeSequencingStimStarts.txt" "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_PhonemeSequencingStimStarts.txt"
            rsync -P "$ORIG_DATA_DIR/ECoG_Task_Data/response_coding/PhonemeSequencingStimStarts.txt" "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_PhonemeSequencingStimStarts.txt"
        fi
        #eeg files
        if ! find "$ORIG_DATA_DIR/D_Data/$TASK/" -iregex ".*$SUB_ID.*\.edf" -exec false {} +  ; then #search for edf files
            find "$ORIG_DATA_DIR/D_Data/$TASK/" -iregex ".*$SUB_ID.*\.edf" -exec cp -v -t "$OUTPUT_DIR/$SUB_ID/" {} +
            find "$OUTPUT_DIR/$SUB_ID/" -regex ".*\.EDF" | xargs -I{} basename -s ".EDF" {} | xargs -I{} mv -v "$OUTPUT_DIR/$SUB_ID/{}.EDF" "$OUTPUT_DIR/$SUB_ID/{}.edf"
            if $ZIP ; then
                find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*\.\(EDF\)\|\(edf\)" | xargs -I{} basename {} | xargs -I{} cut -f 1 -d '.' {} | xargs -I{} echo "$OUTPUT_DIR/$SUB_ID/{}.edf" | xargs -I{} gzip -6 -v {}
            fi
        else #if no edf files find binary files
            x=( $(find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*$SUB_ID.*\.ieeg.dat" -type f | rev | sed -r "s|/|_|" | sed -r "s|/|noisseS/|" | rev | xargs -n 1 basename | sed -r "s|${SUB_ID}_||") )
            y=( $(find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*$SUB_ID.*\.ieeg.dat") )
            z=${#x[@]}
            if $ZIP ; then
                for((i=0;i<=$z-1;i+=1));  do gzip -c -6 -v ${y[$i]} > "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_${x[$i]}.gz" ; done
            else 
                for((i=0;i<=$z-1;i+=1));  do rsync -vP ${y[$i]} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_${x[$i]}" ; done
            fi
        fi
        #find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -type f -regex ".*$SUB_ID.*\.ieeg\.dat" | xargs -n 1 basename | xargs -I{} cp "$OUTPUT_DIR/$SUB_ID/{}" "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_{}"
        find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*experiment\.mat" -exec cp -v -t "$OUTPUT_DIR/$SUB_ID/" {} +
        #event .mat
        find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*\/mat\/.*[tT]rial[(Info)s].*\.mat" -exec cp -v -t "$OUTPUT_DIR/$SUB_ID/" {} +
        #triggers
        #find "$ORIG_DATA_DIR/D_Data/$TASK/$SUB_ID" -regex ".*[Tt]rigger[0-9]?[0-9]?\.mat$" -exec cp -t "$OUTPUT_DIR/$SUB_ID/" {} +
        #rename .mat
        find "$OUTPUT_DIR/$SUB_ID" -type f -regex ".*\.mat" | xargs -n 1 basename | xargs -I{} mv -v "$OUTPUT_DIR/$SUB_ID/{}" "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_{}"
        #rename .dat
        #find "$ORIG_DATA_DIR/ECoG_Task_Data" -type f -regex "Timestamps [MASTER].xlsx"  -exec cp -v {} "$OUTPUT_DIR/$SUB_ID/${SUB_ID}_timstamps.xlsx" \;
        #the big bad python code to convert the renamed files to BIDS
        #requires numpy, nibabel, and pathlib modules

        # # Create necessary directories before writing files
        # mkdir -p "$BIDS_DIR/sub-${SUB_ID}/ieeg"

        python -m data2bids -c config.json -i "$OUTPUT_DIR/$SUB_ID" -o $BIDS_DIR -v || { echo "BIDS conversion for $SUB_ID failed, trying next subject" ; continue; }

		#rm -rf "$OUTPUT_DIR/$SUB_ID"

        [[ $RAN_SUBS =~ (^| )$SUB_ID( |$) ]] || RAN_SUBS+=${SUB_ID}" "
        [[ $RAN_TASKS =~ (^| )$TASK( |$) ]] || RAN_TASKS+=${TASK}" "
        printf "subjects ran: $RAN_SUBS\ntasks ran: $RAN_TASKS\n"    
    done

done
    
# preprocess the BIDS formatted subject
# requires pybids, nipype, 
#cd .. 
#python3 CMRIF_preprocess.py -i $BIDS_DIR -in s${SUB_NUM} -verb || { echo "preprocessing for $SUB_ID failed, trying next subject" ; cd $CWD; continue; }
#cd /BIDS_converter

echo "Finished"