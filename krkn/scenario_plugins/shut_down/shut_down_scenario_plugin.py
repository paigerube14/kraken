import logging
import time
from multiprocessing.pool import ThreadPool
from typing import Optional

import yaml
from krkn_lib.k8s import KrknKubernetes
from krkn_lib.models.telemetry import ScenarioTelemetry
from krkn_lib.telemetry.ocp import KrknTelemetryOpenshift

from krkn.scenario_plugins.abstract_scenario_plugin import AbstractScenarioPlugin
from krkn.scenario_plugins.node_actions.aws_node_scenarios import AWS
from krkn.scenario_plugins.node_actions.az_node_scenarios import Azure
from krkn.scenario_plugins.node_actions.gcp_node_scenarios import GCP
from krkn.scenario_plugins.node_actions.openstack_node_scenarios import OPENSTACKCLOUD
from krkn.scenario_plugins.node_actions.ibmcloud_node_scenarios import IbmCloud
from krkn.rollback.config import RollbackContent
from krkn.rollback.handler import set_rollback_context_decorator

import krkn.scenario_plugins.node_actions.common_node_functions as nodeaction

from krkn_lib.models.k8s import AffectedNodeStatus, AffectedNode

class ShutDownScenarioPlugin(AbstractScenarioPlugin):
    @set_rollback_context_decorator
    def run(
        self,
        run_uuid: str,
        scenario: str,
        lib_telemetry: KrknTelemetryOpenshift,
        scenario_telemetry: ScenarioTelemetry,
    ) -> int:
        try:
            with open(scenario, "r") as f:
                shut_down_config_yaml = yaml.full_load(f)
                shut_down_config_scenario = shut_down_config_yaml[
                    "cluster_shut_down_scenario"
                ]
                affected_nodes_status = AffectedNodeStatus()
                self.cluster_shut_down(
                    shut_down_config_scenario, lib_telemetry.get_lib_kubernetes(), affected_nodes_status
                )

                scenario_telemetry.affected_nodes = affected_nodes_status.affected_nodes
                return 0
        except Exception as e:
            logging.error(
                f"ShutDownScenarioPlugin scenario {scenario} failed with exception: {e}"
            )
            return 1

    @staticmethod
    def rollback_start_instances(
        rollback_content: "RollbackContent",
        lib_telemetry: "Optional[KrknTelemetryOpenshift]",
    ) -> None:
        """
        Start cloud instances without requiring a k8s connection.
        Used as rollback for shut_down scenarios where the entire cluster is offline.
        """
        import logging
        from multiprocessing.pool import ThreadPool
        from krkn.scenario_plugins.node_actions.aws_node_scenarios import AWS
        from krkn.scenario_plugins.node_actions.az_node_scenarios import Azure
        from krkn.scenario_plugins.node_actions.gcp_node_scenarios import GCP
        from krkn.scenario_plugins.node_actions.openstack_node_scenarios import OPENSTACKCLOUD
        from krkn.scenario_plugins.node_actions.ibmcloud_node_scenarios import IbmCloud

        cloud_type = rollback_content.cloud_type
        instance_ids = list(rollback_content.instance_ids) if rollback_content.instance_ids else []

        if not cloud_type:
            logging.error("Rollback: no cloud_type in rollback content, cannot start instances")
            return
        if not instance_ids:
            logging.warning("Rollback: no instance_ids in rollback content, nothing to start")
            return

        if cloud_type.lower() == "aws":
            cloud_object = AWS()
            processes = 0
        elif cloud_type.lower() == "gcp":
            cloud_object = GCP()
            processes = 1
        elif cloud_type.lower() == "openstack":
            cloud_object = OPENSTACKCLOUD()
            processes = 0
        elif cloud_type.lower() in ["azure", "az"]:
            cloud_object = Azure()
            processes = 0
        elif cloud_type.lower() in ["ibm", "ibmcloud"]:
            cloud_object = IbmCloud()
            processes = 0
        else:
            raise RuntimeError(f"Rollback: unsupported cloud type: {cloud_type}")

        logging.info(f"Rollback: starting {len(instance_ids)} instances via {cloud_type}")
        pool_size = processes if processes > 0 else len(instance_ids)
        pool = ThreadPool(processes=pool_size)
        try:
            if instance_ids and isinstance(instance_ids[0], (list, tuple)):
                # Azure: instance_ids are (vm_name, resource_group); start_instances(group_name, vm_name)
                node_id = [n[0] for n in instance_ids]
                node_info = [n[1] for n in instance_ids]
                pool.starmap(cloud_object.start_instances, zip(node_info, node_id))
            else:
                pool.map(cloud_object.start_instances, instance_ids)
        finally:
            pool.close()

    def multiprocess_nodes(self, cloud_object_function, nodes, processes=0):
        try:
            # pool object with number of element

            if processes == 0:
                pool = ThreadPool(processes=len(nodes))
            else:
                pool = ThreadPool(processes=processes)
            if type(nodes[0]) is tuple:
                node_id = []
                node_info = []
                for node in nodes:
                    node_id.append(node[0])
                    node_info.append(node[1])
                logging.info("node id " + str(node_id))
                logging.info("node info" + str(node_info))
                pool.starmap(cloud_object_function, zip(node_info, node_id))

            else:
                logging.info("pool type" + str(type(nodes)))
                pool.map(cloud_object_function, nodes)
            pool.close()
        except Exception as e:
            logging.info("Error on pool multiprocessing: " + str(e))

    # Inject the cluster shut down scenario
    # krkn_lib
    def cluster_shut_down(self, shut_down_config, kubecli: KrknKubernetes, affected_nodes_status: AffectedNodeStatus):
        runs = shut_down_config["runs"]
        shut_down_duration = shut_down_config["shut_down_duration"]
        cloud_type = shut_down_config["cloud_type"]
        timeout = shut_down_config["timeout"]
        processes = 0
        if cloud_type.lower() == "aws":
            cloud_object = AWS()
        elif cloud_type.lower() == "gcp":
            cloud_object = GCP()
            processes = 1
        elif cloud_type.lower() == "openstack":
            cloud_object = OPENSTACKCLOUD()
        elif cloud_type.lower() in ["azure", "az"]:
            cloud_object = Azure()
        elif cloud_type.lower() in ["ibm", "ibmcloud"]:
            cloud_object = IbmCloud()
        else:
            logging.error(
                "Cloud type %s is not currently supported for cluster shut down"
                % cloud_type
            )

            raise RuntimeError()

        nodes = kubecli.list_nodes()
        node_id = []
        for node in nodes:
            instance_id = cloud_object.get_instance_id(node)
            affected_nodes_status.affected_nodes.append(AffectedNode(node, node_id=instance_id))
            node_id.append(instance_id)

        # Register rollback before stopping — cluster will lose k8s connectivity
        rollback_content = RollbackContent(
            cloud_type=cloud_type,
            instance_ids=tuple(node_id),
            skip_kubernetes=True,
        )
        self.rollback_handler.set_rollback_callable(
            self.rollback_start_instances, rollback_content
        )

        for _ in range(runs):
            logging.info("Starting cluster_shut_down scenario injection")
            stopping_nodes = set(node_id)
            self.multiprocess_nodes(cloud_object.stop_instances, node_id, processes)
            stopped_nodes = stopping_nodes.copy()
            start_time = time.time()
            while len(stopping_nodes) > 0:
                for node in stopping_nodes:
                    affected_node = affected_nodes_status.get_affected_node_index(node)

                    if type(node) is tuple:
                        node_status = cloud_object.wait_until_stopped(
                            node[1], node[0], timeout, affected_node
                        )
                    else:
                        node_status = cloud_object.wait_until_stopped(node, timeout, affected_node)

                    # Only want to remove node from stopping list
                    # when fully stopped/no error
                    if node_status:
                        # need to add in time that is passing while waiting for other nodes to be stopped
                        affected_node.set_cloud_stopping_time(time.time() - start_time)
                        stopped_nodes.remove(node)

                stopping_nodes = stopped_nodes.copy()

            logging.info(
                "Shutting down the cluster for the specified duration: %s"
                % shut_down_duration
            )
            time.sleep(shut_down_duration)
            logging.info("Restarting the nodes")
            restarted_nodes = set(node_id)
            self.multiprocess_nodes(cloud_object.start_instances, node_id, processes)
            start_time = time.time()
            logging.info("Wait for each node to be running again")
            not_running_nodes = restarted_nodes.copy()
            while len(not_running_nodes) > 0:
                for node in not_running_nodes:
                    affected_node = affected_nodes_status.get_affected_node_index(node)
                    # need to add in time that is passing while waiting for other nodes to be running

                    if type(node) is tuple:
                        node_status = cloud_object.wait_until_running(
                            node[1], node[0], timeout, affected_node
                        )
                    else:
                        node_status = cloud_object.wait_until_running(node, timeout, affected_node)
                    if node_status:
                        affected_node.set_cloud_running_time(time.time() - start_time)
                        restarted_nodes.remove(node)
                not_running_nodes = restarted_nodes.copy()

            logging.info("Waiting for 150s to allow cluster component initialization")
            time.sleep(150)

            logging.info("Successfully injected cluster_shut_down scenario!")

    def get_scenario_types(self) -> list[str]:
        return ["cluster_shut_down_scenarios"]
