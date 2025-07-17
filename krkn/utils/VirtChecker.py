
import time
import logging
import queue
from datetime import datetime
from krkn_lib.models.telemetry.models import VirtCheck
from krkn.invoke.command import invoke
from krkn.scenario_plugins.kubevirt_vm_outage.kubevirt_vm_outage_scenario_plugin import KubevirtVmOutageScenarioPlugin
from krkn_lib.k8s import KrknKubernetes

class VirtChecker:
    current_iterations: int = 0
    ret_value = 0
    def __init__(self, namespace, iterations, krkn_lib: KrknKubernetes, threads_limt=20):
        self.iterations = iterations
        self.namespace = namespace
        self.krkn_lib = krkn_lib
        
        try:
            self.kube_vm_plugin = KubevirtVmOutageScenarioPlugin()
            self.kube_vm_plugin.init_clients(k8s_client=krkn_lib)
        
        except Exception as e:
            print('exception' + str(e))
        print('namespace ' + str(self.namespace))
        vmis = self.kube_vm_plugin.get_vmis(".*",self.namespace)
        self.threads_limit = threads_limt
        self.vm_list = []
        for vmi in vmis:
            node_name = vmi.get("status",{}).get("nodeName")
            vmi_name = vmi.get("metadata",{}).get("name")
            #self.expose_vm(vmi_name)
            self.vm_list.append(VirtCheck({'vm_name':vmi_name, 'namespace':self.namespace, 'node_name':node_name}))
    
    def expose_vm(self, vm_name: str):
        """
        The method expose vm
        @param vm_name:
        """
        invoke(f'virtctl expose vmi {vm_name} --name {vm_name} -n {self.namespace} --port 27022 --target-port 22 --type NodePort')
        logging.info(f'Exposed {vm_name} in {self.namespace} to port 27022')
    
    def _wait_ssh_vm(self, vm_name: str, vm_node:str):
        """
        This method verifies ssh into VM and return vm node in success or False if failed
        @return:
        """
        # wait till vm ssh login
        if self.get_vm_node(vm_name=vm_name):
            vm_node = self.get_vm_node(vm_name=vm_name)
            if vm_node:
                node_ip = self.get_nodes_addresses()[vm_node]
                vm_node_port = self.get_exposed_vm_port(vm_name=vm_name)
                if self.wait_for_vm_ssh(vm_name=vm_name, node_ip=node_ip, vm_node_port=vm_node_port):
                    logging.info(f"Successfully ssh into VM: '{vm_name}' in Node: '{vm_node}' ")
                return vm_node
        return False

    def wait_for_vm_ssh(self, vm_name: str = '', node_ip: str = '', vm_node_port: str = '',
                        timeout: int = 60):
        """
        This method waits for VM to be accessible via ssh login
        :param vm_name:
        :param node_ip:
        :param vm_node_port:
        :param timeout:
        :return:
        """
        current_wait_time = 0
        while timeout <= 0 or current_wait_time <= timeout:
            check_vm_ssh = f"""if [ "$(ssh -o 'BatchMode=yes' -o ConnectTimeout=1 root@{node_ip} -p {vm_node_port} 2>&1|egrep 'denied|verification failed')" ]; then echo 'True'; else echo 'False'; fi"""
            result = invoke(check_vm_ssh)
            if result == 'True':
                return True
            # sleep for x seconds
            time.sleep(5)
            current_wait_time += 5
    
    def get_vm_ssh_status(self, node_ip: str = '', vm_node_port: str = ''):
        """
        This method returns True when the VM is active and an error message when it is not, using SSH protocol
        SSh VM by node ip and exposed node port
        :param node_ip:
        :param vm_node_port:
        :return:
        """
        ssh_vm_cmd = f"ssh -o 'BatchMode=yes' -o ConnectTimeout=1 root@{node_ip} -p {vm_node_port}"
        check_ssh_vm_cmd = f"""if [ "$(ssh -o 'BatchMode=yes' -o ConnectTimeout=1 root@{node_ip} -p {vm_node_port} 2>&1|egrep 'denied|verification failed')" ]; then echo 'True'; else echo 'False'; fi"""
        if invoke(check_ssh_vm_cmd) == 'True':
            return 'True'
        else:
            return invoke(ssh_vm_cmd)

    def wait_for_vm_access(self, vm_name: str, namespace: str, timeout: int = 60):
        """
        This method waits for VM to be accessible via virtctl ssh
        :param vm_name:
        :param timeout:
        :return:
        """
        current_wait_time = 0
        while timeout <= 0 or current_wait_time <= timeout:
            check_virtctl_vm_cmd = f"virtctl ssh --local-ssh=true --local-ssh-opts='-o BatchMode=yes' --local-ssh-opts='-o PasswordAuthentication=no' --local-ssh-opts='-o ConnectTimeout=2' root@{vm_name} -n {namespace} 2>&1 |egrep 'denied|verification failed'  && echo 'True' || echo 'False'"
            if 'True' in invoke(check_virtctl_vm_cmd):
                return 'True'
            # sleep for x seconds
            time.sleep(5)
            current_wait_time += 5
    
    def get_vm_access(self, vm_name: str = '', namespace: str = ''):
        """
        This method returns True when the VM is access and an error message when it is not, using virtctl protocol
        :param vm_name:
        :param namespace:
        :return: virtctl_status 'True' if successful, or an error message if it fails.
        """
        virtctl_vm_cmd = f"virtctl ssh --local-ssh=true --local-ssh-opts='-o BatchMode=yes' --local-ssh-opts='-o PasswordAuthentication=no' --local-ssh-opts='-o ConnectTimeout=2' root@{vm_name} -n {namespace}"
        check_virtctl_vm_cmd = f"virtctl ssh --local-ssh=true --local-ssh-opts='-o BatchMode=yes' --local-ssh-opts='-o PasswordAuthentication=no' --local-ssh-opts='-o ConnectTimeout=2' root@{vm_name} -n {namespace} 2>&1 |egrep 'denied|verification failed'  && echo 'True' || echo 'False'"
        if 'True' in invoke(check_virtctl_vm_cmd):
            return 'True'
        else:
            return invoke(virtctl_vm_cmd)
    
    def run_virt_check(self, virt_check_config, virt_check_telemetry_queue: queue.Queue):
        response_tracker = {vm.vm_name:True for vm in self.vm_list}
        for vm in self.vm_list:
            virt_check_telemetry = []
            virt_check_tracker = {}
            interval = virt_check_config["interval"] if virt_check_config["interval"] else 2
            
            while self.current_iterations < self.iterations:
                try: 
                    vm_status = self.get_vm_access(vm.vm_name, vm.namespace)
                    print('vm status' + str(vm_status))
                except Exception:
                    response = {}
                    vm_status = False
                
                if vm.vm_name not in virt_check_tracker:
                    start_timestamp = datetime.now()
                    virt_check_tracker[vm['vm_name']] = {
                        "status": vm_status,
                        "start_timestamp": start_timestamp
                    }
                    if not vm_status: 
                        if response_tracker[vm['vm_name']] != False: response_tracker[vm['vm_name']] = False
                    else:
                        if response["status_code"] != virt_check_tracker[vm['vm_name']]["status_code"]:
                            end_timestamp = datetime.now()
                            start_timestamp = virt_check_tracker[vm['vm_name']]["start_timestamp"]
                            duration = (end_timestamp - start_timestamp).total_seconds()
                            change_record = {
                                "vm_name": vm['vm_name'],
                                "namespace": vm['namespace'],
                                "node_name": vm['node_name'],
                                "status": vm_status,
                                "start_timestamp": start_timestamp.isoformat(),
                                "end_timestamp": end_timestamp.isoformat(),
                                "duration": duration
                            }

                            virt_check_telemetry.append(VirtCheck(change_record))
                            if response_tracker[vm['vm_name']] != True: response_tracker[vm['vm_name']] = True
                            del virt_check_tracker[vm['vm_name']]
                time.sleep(interval)
            virt_check_end_time_stamp = datetime.now()
            for url in virt_check_tracker.keys():
                duration = (virt_check_end_time_stamp - virt_check_tracker[url]["start_timestamp"]).total_seconds()
                success_response = {
                    "name": url,
                    "status": True,
                    "start_timestamp": virt_check_tracker[url]["start_timestamp"].isoformat(),
                    "end_timestamp": virt_check_end_time_stamp.isoformat(),
                    "duration": duration
                }
                virt_check_telemetry.append(VirtCheck(success_response))
        print('virt check telem ' + str(virt_check_telemetry))
        virt_check_telemetry_queue.put(virt_check_telemetry)
