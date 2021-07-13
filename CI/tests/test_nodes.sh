set -xeEo pipefail

source CI/tests/common.sh

trap error ERR
trap finish EXIT


function funtional_test_node_crash {
    python3 run_kraken.py -c CI/config/node_config.yaml
    echo "${test_name} test: Success"
}

funtional_test_node_crash

