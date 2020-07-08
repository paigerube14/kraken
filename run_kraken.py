#!/usr/bin/env python

import sys
import os
import time
import optparse
import logging
import yaml
import requests
import kraken.kubernetes.client as kubecli
import kraken.invoke.command as runcommand
import pyfiglet


# Stop all running network scenarios
def stop_network_scenarios(network_scenarios):

    for scenario in network_scenarios:
        runcommand.invoke("kubectl delete -f " + str(scenario))


# Main function
def main(cfg):
    # Start kraken
    print(pyfiglet.figlet_format("kraken"))
    logging.info("Starting kraken")

    # Parse and read the config
    if os.path.isfile(cfg):
        with open(cfg, 'r') as f:
            config = yaml.full_load(f)
        kubeconfig_path = config["kraken"]["kubeconfig_path"]
        scenarios = config["kraken"]["scenarios"]
        network_scenarios = config["kraken"]["network_scenarios"]
        cerberus_enabled = config["cerberus"]["cerberus_enabled"]
        wait_duration = config["tunings"]["wait_duration"]
        iterations = config["tunings"]["iterations"]
        daemon_mode = config["tunings"]['daemon_mode']

        # Initialize clients
        if not os.path.isfile(kubeconfig_path):
            kubeconfig_path = None
        logging.info("Initializing client to talk to the Kubernetes cluster")
        kubecli.initialize_clients(kubeconfig_path)

        # find node kraken might be running on
        kubecli.find_kraken_node()


        # if network scenarios, see if chaos testing namespace
        '''if network_scenarios:
        curl -sSL https://raw.githubusercontent.com/pingcap/chaos-mesh/master/examples/web-show/deploy.sh | bash
            output = runcommand.invoke("kubectl get namespaces | grep chaos-testing")
            if output:
                runcommand.invoke("curl -sSL https://raw.githubusercontent.com/pingcap/chaos-mesh/master/install.sh | bash -s -- --local kind")
'''
        # Cluster info
        logging.info("Fetching cluster info")
        cluster_version = runcommand.invoke("kubectl get clusterversion")
        cluster_info = runcommand.invoke("kubectl cluster-info | awk 'NR==1' | sed -r "
                                         "'s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g'")  # noqa
        logging.info("\n%s%s" % (cluster_version, cluster_info))

        # Initialize the start iteration to 0
        iteration = 0

        # Set the number of iterations to loop to infinity if daemon mode is
        # enabled or else set it to the provided iterations count in the config
        if daemon_mode:
            logging.info("Daemon mode enabled, kraken will cause chaos forever")
            logging.info("Ignoring the iterations set")
            iterations = float('inf')
        else:
            logging.info("Daemon mode not enabled, will run through %s iterations"
                         % str(iterations))
            iterations = int(iterations)

        running_network_scenarios = []
        # Loop to run the chaos starts here
        while (int(iteration) < iterations):
            # Inject chaos scenarios specified in the config
            try:
                # Loop to run the scenarios starts here
                for scenario in scenarios:
                    logging.info("Injecting scenario: %s" % (scenario))
                    runcommand.invoke("powerfulseal autonomous --use-pod-delete-instead-of-ssh-kill"
                                      " --policy-file %s --kubeconfig %s --no-cloud"
                                      " --inventory-kubernetes --headless"
                                      % (scenario, kubeconfig_path))
                    logging.info("Scenario: %s has been successfully injected!" % (scenario))

                    if cerberus_enabled:
                        cerberus_url = config["cerberus"]["cerberus_url"]
                        if not cerberus_url:
                            logging.error("url where Cerberus publishes True/False signal "
                                          "is not provided.")
                            sys.exit(1)
                        cerberus_status = requests.get(cerberus_url).content
                        cerberus_status = True if cerberus_status == b'True' else False
                        if not cerberus_status:
                            logging.error("Received a no-go signal from Cerberus, looks like the"
                                          " cluster is unhealthy. Please check the Cerberus report"
                                          " for more details. Test failed.")
                            sys.exit(1)
                        else:
                            logging.info("Received a go signal from Ceberus, the cluster is "
                                         "healthy. Test passed.")
                    logging.info("Waiting for the specified duration: %s" % (wait_duration))
                    time.sleep(wait_duration)
            except Exception as e:
                logging.error("Failed to run scenario: %s. Encountered the following exception: %s"
                              % (scenario, e))
            try:
                # Loop to run the scenarios starts here
                for net_scenario in network_scenarios:
                    logging.info("Injecting scenario: %s" % (net_scenario))
                    runcommand.invoke("kubectl apply -f %s" % (net_scenario))
                    running_network_scenarios.append(net_scenario)
                    logging.info("Scenario: %s has been successfully injected!" % (net_scenario))
                    logging.info("Waiting for the specified duration: %s" % (wait_duration))
                    time.sleep(wait_duration)
            except Exception as e:
                logging.error("Failed to run scenario: %s. Encountered the following exception: %s"
                              % (scenario, e))
            iteration += 1
        #stop_network_scenarios(running_network_scenarios)

        #curl -sSL https://raw.githubusercontent.com/pingcap/chaos-mesh/master/install.sh | bash -s -- --template | kubectl delete -f -
        #  curl -sSL https://raw.githubusercontent.com/pingcap/chaos-mesh/master/examples/web-show/deploy.sh | bash -s -- -d
    else:
        logging.error("Cannot find a config at %s, please check" % (cfg))
        sys.exit(1)


if __name__ == "__main__":
    # Initialize the parser to read the config
    parser = optparse.OptionParser()
    parser.add_option(
        "-c", "--config",
        dest="cfg",
        help="config location",
        default="config/config.yaml",
    )
    (options, args) = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("kraken.report", mode='w'),
            logging.StreamHandler()
        ]
    )
    if (options.cfg is None):
        logging.error("Please check if you have passed the config")
        sys.exit(1)
    else:
        main(options.cfg)
