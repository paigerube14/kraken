#!/bin/bash

ROOT_FOLDER="/home/krkn"
KRAKEN_FOLDER="$ROOT_FOLDER/kraken"

source $ROOT_FOLDER/main_env.sh
source $ROOT_FOLDER/env.sh
source $ROOT_FOLDER/common_run.sh

if [[ $KRKN_DEBUG == "True" ]]; then
  set -ex
fi

# TODO: Add envsubst template substitution and run logic
# Example:
# envsubst < $KRAKEN_FOLDER/config/config.yaml.template > $KRAKEN_FOLDER/config/scenario_config.yaml

checks

cd $KRAKEN_FOLDER
extra_var=""
if [[ $KRKN_DEBUG == "True" ]]; then
  extra_var="--debug True"
fi

# TODO: Update config file name below
python3.11 run_kraken.py --config=config/scenario_config.yaml $extra_var
