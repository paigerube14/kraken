import time
import os
import logging
import krkn.scenario_plugins.node_actions.common_node_functions as nodeaction
from krkn.scenario_plugins.node_actions.abstract_node_scenarios import (
    abstract_node_scenarios,
)
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.identity import DefaultAzureCredential
from krkn_lib.k8s import KrknKubernetes
from krkn_lib.models.k8s import AffectedNode, AffectedNodeStatus

class Azure:
    def __init__(self):
        logging.info("azure " + str(self))
        # Acquire a credential object using CLI-based authentication.
        credentials = DefaultAzureCredential()
        logging.info("credential " + str(credentials))
        # az_account = runcommand.invoke("az account list -o yaml")
        # az_account_yaml = yaml.safe_load(az_account, Loader=yaml.FullLoader)
        logger = logging.getLogger("azure")
        logger.setLevel(logging.WARNING)
        subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        self.compute_client = ComputeManagementClient(credentials, subscription_id,logging=logger)
        self.network_client = NetworkManagementClient(credentials, subscription_id)
        

    # Get the instance ID of the node
    def get_instance_id(self, node_name):
        vm_list = self.compute_client.virtual_machines.list_all()
        for vm in vm_list:
            array = vm.id.split("/")
            resource_group = array[4]
            vm_name = array[-1]
            if node_name == vm_name:
                return vm_name, resource_group
        logging.error("Couldn't find vm with name " + str(node_name))

    # Start the node instance
    def start_instances(self, group_name, vm_name):
        try:
            self.compute_client.virtual_machines.begin_start(group_name, vm_name)
            logging.info("vm name " + str(vm_name) + " started")
        except Exception as e:
            logging.error(
                "Failed to start node instance %s. Encountered following "
                "exception: %s." % (vm_name, e)
            )
            raise RuntimeError()

    # Stop the node instance
    def stop_instances(self, group_name, vm_name):
        try:
            self.compute_client.virtual_machines.begin_power_off(group_name, vm_name)
            logging.info("vm name " + str(vm_name) + " stopped")
        except Exception as e:
            logging.error(
                "Failed to stop node instance %s. Encountered following "
                "exception: %s." % (vm_name, e)
            )
            raise RuntimeError()

    # Terminate the node instance
    def terminate_instances(self, group_name, vm_name):
        try:
            self.compute_client.virtual_machines.begin_delete(group_name, vm_name)
            logging.info("vm name " + str(vm_name) + " terminated")
        except Exception as e:
            logging.error(
                "Failed to terminate node instance %s. Encountered following "
                "exception: %s." % (vm_name, e)
            )

            raise RuntimeError()

    # Reboot the node instance
    def reboot_instances(self, group_name, vm_name):
        try:
            self.compute_client.virtual_machines.begin_restart(group_name, vm_name)
            logging.info("vm name " + str(vm_name) + " rebooted")
        except Exception as e:
            logging.error(
                "Failed to reboot node instance %s. Encountered following "
                "exception: %s." % (vm_name, e)
            )

            raise RuntimeError()

    def get_vm_status(self, resource_group, vm_name):
        statuses = self.compute_client.virtual_machines.instance_view(
            resource_group, vm_name
        ).statuses
        status = len(statuses) >= 2 and statuses[1]
        return status

    # Wait until the node instance is running
    def wait_until_running(self, resource_group, vm_name, timeout, affected_node):
        time_counter = 0
        start_time = time.time()
        status = self.get_vm_status(resource_group, vm_name)
        while status and status.code != "PowerState/running":
            status = self.get_vm_status(resource_group, vm_name)
            logging.info("Vm %s is still not running, sleeping for 5 seconds" % vm_name)
            time.sleep(5)
            time_counter += 5
            if time_counter >= timeout:
                logging.info("Vm %s is still not ready in allotted time" % vm_name)
                return False
        end_time = time.time()
        if affected_node:
            affected_node.set_affected_node_status("running", end_time - start_time)
        return True

    # Wait until the node instance is stopped
    def wait_until_stopped(self, resource_group, vm_name, timeout, affected_node):
        time_counter = 0
        start_time = time.time()
        status = self.get_vm_status(resource_group, vm_name)
        while status and status.code != "PowerState/stopped":
            status = self.get_vm_status(resource_group, vm_name)
            logging.info("Vm %s is still stopping, sleeping for 5 seconds" % vm_name)
            time.sleep(5)
            time_counter += 5
            if time_counter >= timeout:
                logging.info("Vm %s is still not stopped in allotted time" % vm_name)
                return False
        end_time = time.time()
        if affected_node:
            affected_node.set_affected_node_status("stopped", end_time - start_time)
        return True

    # Wait until the node instance is terminated
    def wait_until_terminated(self, resource_group, vm_name, timeout, affected_node):
        start_time = time.time()
        statuses = self.compute_client.virtual_machines.instance_view(
            resource_group, vm_name
        ).statuses[0]
        logging.info("vm status " + str(statuses))
        time_counter = 0
        while statuses.code == "ProvisioningState/deleting":
            try:
                statuses = self.compute_client.virtual_machines.instance_view(
                    resource_group, vm_name
                ).statuses[0]
                logging.info("Vm %s is still deleting, waiting 10 seconds" % vm_name)
                time.sleep(10)
                time_counter += 10
                if time_counter >= timeout:
                    logging.info("Vm %s was not terminated in allotted time" % vm_name)
                    return False
            except Exception:
                logging.info("Vm %s is terminated" % vm_name)
                end_time = time.time()
                if affected_node:
                    affected_node.set_affected_node_status("terminated", end_time - start_time)
                return True

    # Describe network acl
    def describe_network_acls(self, vpc_id, subnet_id):
        try:
            # network security group 
            response = self.compute_client.virtual_machines.describe_network_acls(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "association.subnet-id", "Values": [subnet_id]},
                ]
            )
        except Exception as e:
            logging.error(
                "Failed to describe network acl: %s."
                "Make sure you have aws cli configured on the host and set for the region of your vpc/subnet"
                % (e)
            )

            raise RuntimeError()
        associations = response["NetworkAcls"][0]["Associations"]
        # grab the current network_acl in use
        original_acl_id = response["NetworkAcls"][0]["Associations"][0]["NetworkAclId"]
        return associations, original_acl_id

    # Delete network acl
    def delete_network_acl(self, acl_id):
        try:
            logging.info("Deleting the network acl: %s" % (acl_id))
            self.compute_client.virtual_machines.delete_network_acl(NetworkAclId=acl_id)
        except Exception as e:
            logging.error(
                "Failed to delete network_acl %s: %s"
                "Make sure you have aws cli configured on the host and set for the region of your vpc/subnet"
                % (acl_id, e)
            )

            raise RuntimeError()

    # Detach volume
    def detach_volumes(self, volumes_ids: list):
        for volume in volumes_ids:
            try:
                self.compute_client.virtual_machines.(VolumeId=volume, Force=True)
            except Exception as e:
                logging.error(
                    "Detaching volume %s failed with exception: %s"
                    % (volume, e)
                )

    # Attach volume
    def attach_volume(self, attachment: dict):
        try:
            if self.get_volume_state(attachment["VolumeId"]) == "in-use":
                logging.info(
                    "Volume %s is already in use." % attachment["VolumeId"]
                )
                return
            logging.info(
                "Attaching the %s volumes to instance %s."
                % (attachment["VolumeId"], attachment["InstanceId"])
            )
            self.compute_client.virtual_machines.attach_volume(
                InstanceId=attachment["InstanceId"],
                Device=attachment["Device"],
                VolumeId=attachment["VolumeId"]
            )
        except Exception as e:
            logging.error(
                "Failed attaching disk %s to the %s instance. "
                "Encountered following exception: %s"
                % (attachment['VolumeId'], attachment['InstanceId'], e)
            )
            raise RuntimeError()

    # Get IDs of node volumes
    def get_volumes_ids(self, instance_id: list):
        response = self.describe_instances(InstanceIds=instance_id)
        instance_attachment_details = response["Reservations"][0]["Instances"][0]["BlockDeviceMappings"]
        root_volume_device_name = self.get_root_volume_id(instance_id)
        volume_ids = []
        for device in instance_attachment_details:
            if device["DeviceName"] != root_volume_device_name:
                volume_id = device["Ebs"]["VolumeId"]
                volume_ids.append(volume_id)
        return volume_ids

    # Get volumes attachment details
    def get_volume_attachment_details(self, volume_ids: list):
        response = self.compute_client.virtual_machines.describe_volumes(VolumeIds=volume_ids)
        volumes_details = response["Volumes"]
        return volumes_details

    # Get root volume
    def get_root_volume_id(self, instance_id):
        instance_id = instance_id[0]
        instance = self.boto_resource.Instance(instance_id)
        root_volume_id = instance.root_device_name
        return root_volume_id

    # Get volume state
    def get_volume_state(self, volume_id: str):
        volume = self.boto_resource.Volume(volume_id)
        state = volume.state
        return state
# krkn_lib
class azure_node_scenarios(abstract_node_scenarios):
    def __init__(self, kubecli: KrknKubernetes, affected_nodes_status: AffectedNodeStatus):
        super().__init__(kubecli, affected_nodes_status)
        logging.info("init in azure")
        self.azure = Azure()
        

    # Node scenario to start the node
    def node_start_scenario(self, instance_kill_count, node, timeout):
        for _ in range(instance_kill_count):
            affected_node = AffectedNode(node)
            try:
                logging.info("Starting node_start_scenario injection")
                vm_name, resource_group = self.azure.get_instance_id(node)
                affected_node.node_id = vm_name
                logging.info(
                    "Starting the node %s with instance ID: %s "
                    % (vm_name, resource_group)
                )
                self.azure.start_instances(resource_group, vm_name)
                self.azure.wait_until_running(resource_group, vm_name, timeout, affected_node=affected_node)
                nodeaction.wait_for_ready_status(vm_name, timeout, self.kubecli, affected_node)
                logging.info("Node with instance ID: %s is in running state" % node)
                logging.info("node_start_scenario has been successfully injected!")
            except Exception as e:
                logging.error(
                    "Failed to start node instance. Encountered following "
                    "exception: %s. Test Failed" % (e)
                )
                logging.error("node_start_scenario injection failed!")

                raise RuntimeError()
            self.affected_nodes_status.affected_nodes.append(affected_node)

    # Node scenario to stop the node
    def node_stop_scenario(self, instance_kill_count, node, timeout):
        for _ in range(instance_kill_count):
            affected_node = AffectedNode(node)
            try:
                logging.info("Starting node_stop_scenario injection")
                vm_name, resource_group = self.azure.get_instance_id(node)
                affected_node.node_id = vm_name
                logging.info(
                    "Stopping the node %s with instance ID: %s "
                    % (vm_name, resource_group)
                )
                self.azure.stop_instances(resource_group, vm_name)
                self.azure.wait_until_stopped(resource_group, vm_name, timeout, affected_node=affected_node)
                logging.info("Node with instance ID: %s is in stopped state" % vm_name)
                nodeaction.wait_for_unknown_status(vm_name, timeout, self.kubecli, affected_node)
            except Exception as e:
                logging.error(
                    "Failed to stop node instance. Encountered following exception: %s. "
                    "Test Failed" % e
                )
                logging.error("node_stop_scenario injection failed!")

                raise RuntimeError()
            self.affected_nodes_status.affected_nodes.append(affected_node)

    # Node scenario to terminate the node
    def node_termination_scenario(self, instance_kill_count, node, timeout):
        for _ in range(instance_kill_count):
            affected_node = AffectedNode(node)
            try:
                logging.info("Starting node_termination_scenario injection")
                vm_name, resource_group = self.azure.get_instance_id(node)
                affected_node.node_id = vm_name
                logging.info(
                    "Terminating the node %s with instance ID: %s "
                    % (vm_name, resource_group)
                )
                self.azure.terminate_instances(resource_group, vm_name)
                self.azure.wait_until_terminated(resource_group, vm_name, timeout, affected_node)
                for _ in range(timeout):
                    if vm_name not in self.kubecli.list_nodes():
                        break
                    time.sleep(1)
                if vm_name in self.kubecli.list_nodes():
                    raise Exception("Node could not be terminated")
                logging.info("Node with instance ID: %s has been terminated" % node)
                logging.info(
                    "node_termination_scenario has been successfully injected!"
                )
            except Exception as e:
                logging.error(
                    "Failed to terminate node instance. Encountered following exception:"
                    " %s. Test Failed" % (e)
                )
                logging.error("node_termination_scenario injection failed!")

                raise RuntimeError()
            self.affected_nodes_status.affected_nodes.append(affected_node)


    # Node scenario to reboot the node
    def node_reboot_scenario(self, instance_kill_count, node, timeout):
        for _ in range(instance_kill_count):
            affected_node = AffectedNode(node)
            try:
                logging.info("Starting node_reboot_scenario injection")
                vm_name, resource_group = self.azure.get_instance_id(node)
                affected_node.node_id = vm_name
                logging.info(
                    "Rebooting the node %s with instance ID: %s "
                    % (vm_name, resource_group)
                )
                
                self.azure.reboot_instances(resource_group, vm_name)

                nodeaction.wait_for_ready_status(vm_name, timeout, self.kubecli, affected_node)

                logging.info("Node with instance ID: %s has been rebooted" % (vm_name))
                logging.info("node_reboot_scenario has been successfully injected!")
            except Exception as e:
                logging.error(
                    "Failed to reboot node instance. Encountered following exception:"
                    " %s. Test Failed" % (e)
                )
                logging.error("node_reboot_scenario injection failed!")

                raise RuntimeError()
            self.affected_nodes_status.affected_nodes.append(affected_node)
