#!/bin/bash

# --- Configuration ---
SCRIPT_NAME="test_link_evaluation.py"
TOTAL_RUNS=290  # How many times to restart the process
COUNT=1

echo "Starting batch processing: $TOTAL_RUNS iterations."

# --- Loop ---
while [ $COUNT -le $TOTAL_RUNS ]
do
    echo "------------------------------------------------"
    echo "Iteration $COUNT of $TOTAL_RUNS"
    echo "Starting Python process..."
    
    # Run the python script
    python "$SCRIPT_NAME"
    
    # Check if the python script exited with an error
    if [ $? -ne 0 ]; then
        echo "Python script crashed. Stopping loop."
        exit 1
    fi

    echo "Process finished iteration $COUNT."
    ((COUNT++))
done

echo "------------------------------------------------"
echo "All $TOTAL_RUNS iterations completed successfully."