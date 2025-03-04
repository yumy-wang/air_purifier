#!/bin/bash

nohup python3 -u adjust_purifier_by_co2_v250208.py > log.log 2>&1 &
echo "Script started, check log.log for details."
