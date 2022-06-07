#!/bin/bash
set -x

config="/root/.kube/config"
#optional parameters
while [[ $# -gt 1 ]]
do
  key="$1"
  echo "key $key"

  case $key in
      -c|--config)
      config=$2
      shift # past argument
      shift # past value
      ;;
esac
done

export test_kube_config_path=$config

ci_tests_loc="CI/tests/my_tests"

echo "running test suit consisting of ${ci_tests}"

rm -rf CI/out

mkdir CI/out

results_file_name="results.markdown"

rm -f CI/$results_file_name

results="CI/$results_file_name"

# Prep the results.markdown file
echo 'Test                   | Result | Duration | Output' >> $results
echo '-----------------------|--------|---------|-----------' >> $results

# Run each test
for test_name in `cat CI/tests/my_tests`
do
  ./CI/run_test.sh $test_name $results
done
