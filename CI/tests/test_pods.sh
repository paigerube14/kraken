set -xeEo pipefail

source CI/tests/common.sh

trap error ERR
trap finish EXIT

pod_file=CI/scenarios/hello_pod.yaml


function funtional_test_pod_deletion {
    python3 run_kraken.py -c CI/config/config.yaml
    echo "${test_name} test: Success"
}

oc create -f $pod_file
funtional_test_pod_deletion
oc delete -f $pod_file
