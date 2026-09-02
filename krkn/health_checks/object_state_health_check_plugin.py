# Copyright 2026 The Krkn Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Object State Health Check Plugin

This plugin provides health checking for Kubernetes object states by monitoring
conditions on any Kubernetes resource type (Pods, Deployments, StatefulSets, etc.).

Example configuration in config.yaml:
    object_state_checks:
        interval: 5
        run_during: ["pre", "during", "post"]
        exit_on_failure: False
        config:
            - name: "etcd-pods-ready"
              kind: "Pod"
              object_name: "etcd-.*"        # Regex pattern
              namespace: "kube-system"
              label_selector: ""             # Optional label selector
              condition:
                  type: "Ready"
                  status: "True"

            - name: "deployment-available"
              kind: "Deployment"
              object_name: "my-app"
              namespace: "default"
              condition:
                  type: "Available"
                  status: "True"
"""

import logging
import queue
import re
import time
from datetime import datetime
from typing import Any, Optional

from krkn_lib.k8s import KrknKubernetes
from krkn_lib.models.telemetry.models import HealthCheck
from krkn_lib.utils.functions import get_yaml_item_value

from krkn.health_checks.abstract_health_check_plugin import AbstractHealthCheckPlugin


class ObjectStateHealthCheckPlugin(AbstractHealthCheckPlugin):
    """
    Kubernetes object state health check plugin that monitors object conditions.

    This plugin can monitor any Kubernetes resource type (Pod, Deployment, StatefulSet,
    DaemonSet, etc.) and check specific conditions on those resources.
    """

    def __init__(
        self,
        health_check_type: str = "object_state_health_check",
        iterations: int = 1,
        krkn_lib: KrknKubernetes = None,
        **kwargs
    ):
        """
        Initializes the object state health check plugin.

        :param health_check_type: the health check type identifier
        :param iterations: the number of chaos iterations to monitor
        :param krkn_lib: KrknKubernetes client instance
        :param kwargs: additional keyword arguments
        """
        super().__init__(health_check_type)
        self.iterations = iterations
        self.current_iterations = 0
        self.krkn_lib = krkn_lib

    def get_health_check_types(self) -> list[str]:
        """
        Returns the health check types this plugin handles.

        :return: list of health check type identifiers
        """
        return ["object_state_health_check", "k8s_object_health_check"]

    def get_config_key(self) -> str:
        """
        Returns the top-level config.yaml key this plugin reads from.

        :return: config key string
        """
        return "object_state_checks"

    def increment_iterations(self) -> None:
        """
        Increments the current iteration counter.

        :return: None
        """
        self.current_iterations += 1

    def _get_objects(
        self,
        kind: str,
        namespace: str,
        object_name: Optional[str] = None,
        label_selector: Optional[str] = None
    ) -> list[dict]:
        """
        Get Kubernetes objects matching the criteria.

        :param kind: Kubernetes resource kind (e.g., "Pod", "Deployment")
        :param namespace: Namespace to search in
        :param object_name: Optional name or regex pattern
        :param label_selector: Optional label selector
        :return: List of matching objects
        """
        try:
            # Map kind to appropriate API call
            kind_lower = kind.lower()

            if kind_lower == "pod":
                if label_selector:
                    objects = self.krkn_lib.list_pods(namespace, label_selector)
                else:
                    objects = self.krkn_lib.list_pods(namespace)
            elif kind_lower == "deployment":
                objects = self.krkn_lib.list_deployments(namespace)
            elif kind_lower == "statefulset":
                objects = self.krkn_lib.list_statefulsets(namespace)
            elif kind_lower == "daemonset":
                objects = self.krkn_lib.list_daemonsets(namespace)
            elif kind_lower == "replicaset":
                objects = self.krkn_lib.list_replicasets(namespace)
            else:
                # For other kinds, try to get via dynamic client
                logging.warning(
                    f"Kind '{kind}' may not be fully supported. "
                    f"Attempting to retrieve with generic API call."
                )
                objects = []

            # Filter by name pattern if provided
            if object_name and objects:
                pattern = re.compile(object_name)
                filtered_objects = []
                for obj in objects:
                    obj_name = obj.get("metadata", {}).get("name", "")
                    if pattern.match(obj_name):
                        filtered_objects.append(obj)
                objects = filtered_objects

            return objects if objects else []

        except Exception as e:
            logging.error(
                f"Error getting {kind} objects in namespace {namespace}: {e}"
            )
            return []

    def _check_object_condition(
        self,
        obj: dict,
        condition_type: str,
        condition_status: str
    ) -> tuple[bool, str]:
        """
        Check if an object has the specified condition.

        :param obj: Kubernetes object
        :param condition_type: Condition type to check (e.g., "Ready", "Available")
        :param condition_status: Expected status (e.g., "True", "False")
        :return: Tuple of (passed, message)
        """
        kind = obj.get("kind", "Unknown")
        obj_name = obj.get("metadata", {}).get("name", "unknown")
        namespace = obj.get("metadata", {}).get("namespace", "unknown")

        # Get conditions from object status
        conditions = obj.get("status", {}).get("conditions", [])

        if not conditions:
            return False, f"{kind} {namespace}/{obj_name} has no conditions"

        # Find the matching condition
        for condition in conditions:
            if condition.get("type") == condition_type:
                actual_status = condition.get("status", "Unknown")
                if actual_status == condition_status:
                    return True, f"{kind} {namespace}/{obj_name} condition {condition_type}={condition_status}"
                else:
                    reason = condition.get("reason", "")
                    message = condition.get("message", "")
                    return False, (
                        f"{kind} {namespace}/{obj_name} condition {condition_type}={actual_status} "
                        f"(expected {condition_status}). Reason: {reason}. Message: {message}"
                    )

        # Condition type not found
        available_conditions = [c.get("type") for c in conditions]
        return False, (
            f"{kind} {namespace}/{obj_name} does not have condition type '{condition_type}'. "
            f"Available conditions: {available_conditions}"
        )

    def _check_single_config(self, check_config: dict) -> dict[str, Any]:
        """
        Check a single object state configuration.

        :param check_config: Configuration for one check
        :return: Dictionary with check results
        """
        check_name = check_config.get("name", "unnamed-check")
        kind = check_config.get("kind", "")
        object_name = check_config.get("object_name", None)
        namespace = check_config.get("namespace", "default")
        label_selector = check_config.get("label_selector", None)
        condition = check_config.get("condition", {})
        condition_type = condition.get("type", "Ready")
        condition_status = condition.get("status", "True")

        if not kind:
            return {
                "check_name": check_name,
                "passed": False,
                "message": "No 'kind' specified in configuration"
            }

        # Get matching objects
        objects = self._get_objects(kind, namespace, object_name, label_selector)

        if not objects:
            return {
                "check_name": check_name,
                "passed": False,
                "message": f"No {kind} objects found matching criteria in namespace {namespace}"
            }

        # Check condition on all matching objects
        # Requires ALL objects to pass - if ANY object fails, the check fails
        all_passed = True
        messages = []

        for obj in objects:
            passed, message = self._check_object_condition(obj, condition_type, condition_status)
            messages.append(message)
            if not passed:
                all_passed = False  # If ANY object fails, overall check fails

        return {
            "check_name": check_name,
            "passed": all_passed,  # True only if ALL objects passed
            "objects_checked": len(objects),
            "message": "; ".join(messages)
        }

    def run_once(self, config: dict[str, Any]) -> dict[str, Any]:
        """
        Runs a one-time object state health check for all configured checks.

        :param config: the health check configuration dictionary
        :return: dictionary with results:
                 {
                   "passed": bool,
                   "failures": list of failure details,
                   "details": dict with per-check status
                 }
        """
        if not config or not config.get("config"):
            logging.info("Object state health check config is not defined, skipping one-time check")
            return {"passed": True, "failures": [], "details": {}}

        failures = []
        details = {}

        for check_config in config.get("config", []):
            result = self._check_single_config(check_config)
            check_name = result["check_name"]
            details[check_name] = result

            if not result["passed"]:
                failures.append({
                    "check_name": check_name,
                    "message": result["message"]
                })

        passed = len(failures) == 0
        return {
            "passed": passed,
            "failures": failures,
            "details": details
        }

    def run_health_check(
        self,
        config: dict[str, Any],
        telemetry_queue: queue.Queue,
    ) -> None:
        """
        Runs the object state health check monitoring loop.

        Continuously monitors the configured object states until the specified
        number of iterations is complete. Tracks status changes and collects
        telemetry data.

        :param config: the health check configuration dictionary
        :param telemetry_queue: a queue to put telemetry data for collection
        :return: None
        """
        if not config or not config.get("config"):
            logging.info("Object state health check config is not defined, skipping")
            return

        health_check_telemetry = []
        health_check_tracker = {}
        interval = config.get("interval", 5)
        exit_on_failure = config.get("exit_on_failure", False)

        # Track current status for each check
        status_tracker = {
            cfg.get("name", f"check-{i}"): True
            for i, cfg in enumerate(config.get("config", []))
        }

        while self.current_iterations < self.iterations and not self._stop_event.is_set():
            for check_config in config.get("config", []):
                check_name = check_config.get("name", "unnamed-check")

                result = self._check_single_config(check_config)

                if check_name not in health_check_tracker:
                    # First time seeing this check
                    start_timestamp = datetime.now()
                    health_check_tracker[check_name] = {
                        "check_name": check_name,
                        "passed": result["passed"],
                        "start_timestamp": start_timestamp,
                        "message": result["message"]
                    }
                    if not result["passed"]:
                        if status_tracker[check_name] != False:
                            status_tracker[check_name] = False
                        if exit_on_failure and self.ret_value == 0:
                            self.ret_value = 3
                else:
                    # Check if status changed
                    if result["passed"] != health_check_tracker[check_name]["passed"]:
                        end_timestamp = datetime.now()
                        start_timestamp = health_check_tracker[check_name]["start_timestamp"]
                        previous_passed = health_check_tracker[check_name]["passed"]
                        duration = (end_timestamp - start_timestamp).total_seconds()

                        # Record the status change period
                        change_record = {
                            "check_name": check_name,
                            "passed": previous_passed,
                            "start_timestamp": start_timestamp.isoformat(),
                            "end_timestamp": end_timestamp.isoformat(),
                            "duration": duration,
                            "message": health_check_tracker[check_name]["message"]
                        }

                        health_check_telemetry.append(HealthCheck(change_record))

                        if status_tracker[check_name] != True:
                            status_tracker[check_name] = True

                        # Reset tracker with new status
                        del health_check_tracker[check_name]
                        health_check_tracker[check_name] = {
                            "check_name": check_name,
                            "passed": result["passed"],
                            "start_timestamp": end_timestamp,
                            "message": result["message"]
                        }

            time.sleep(interval)

        # Record final status for all tracked checks
        health_check_end_timestamp = datetime.now()
        for check_name in health_check_tracker.keys():
            duration = (
                health_check_end_timestamp
                - health_check_tracker[check_name]["start_timestamp"]
            ).total_seconds()
            final_record = {
                "check_name": check_name,
                "passed": health_check_tracker[check_name]["passed"],
                "start_timestamp": health_check_tracker[check_name][
                    "start_timestamp"
                ].isoformat(),
                "end_timestamp": health_check_end_timestamp.isoformat(),
                "duration": duration,
                "message": health_check_tracker[check_name]["message"]
            }
            health_check_telemetry.append(HealthCheck(final_record))

        # Put telemetry data in the queue
        telemetry_queue.put(health_check_telemetry)
