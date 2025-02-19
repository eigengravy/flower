# Copyright 2022 Flower Labs GmbH. All Rights Reserved.
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
# ==============================================================================
"""Flower Driver app tests."""


import threading
import time
import unittest
from unittest.mock import MagicMock

from flwr.proto.driver_pb2 import (  # pylint: disable=E0611
    CreateRunResponse,
    GetNodesResponse,
)
from flwr.proto.node_pb2 import Node  # pylint: disable=E0611
from flwr.server.client_manager import SimpleClientManager

from .app import update_client_manager


class TestClientManagerWithDriver(unittest.TestCase):
    """Tests for ClientManager.

    Considering multi-threading, all tests assume that the `update_client_manager()`
    updates the ClientManager every 3 seconds.
    """

    def test_simple_client_manager_update(self) -> None:
        """Tests if the node update works correctly."""
        # Prepare
        expected_nodes = [Node(node_id=i, anonymous=False) for i in range(100)]
        expected_updated_nodes = [
            Node(node_id=i, anonymous=False) for i in range(80, 120)
        ]
        driver = MagicMock()
        driver.stub = "driver stub"
        driver.create_run.return_value = CreateRunResponse(run_id=1)
        driver.get_nodes.return_value = GetNodesResponse(nodes=expected_nodes)
        client_manager = SimpleClientManager()
        lock = threading.Lock()
        f_stop = threading.Event()
        # Execute
        thread = threading.Thread(
            target=update_client_manager,
            args=(driver, client_manager, lock, f_stop),
            daemon=True,
        )
        thread.start()
        # Wait until all nodes are registered via `client_manager.sample()`
        client_manager.sample(len(expected_nodes))
        # Retrieve all nodes in `client_manager`
        node_ids = {proxy.node_id for proxy in client_manager.all().values()}
        # Update the GetNodesResponse and wait until the `client_manager` is updated
        driver.get_nodes.return_value = GetNodesResponse(nodes=expected_updated_nodes)
        while True:
            with lock:
                if len(client_manager.all()) == len(expected_updated_nodes):
                    break
            time.sleep(1.3)
        # Retrieve all nodes in `client_manager`
        updated_node_ids = {proxy.node_id for proxy in client_manager.all().values()}
        # Simulate `driver.disconnect()`
        driver.stub = None

        # Assert
        driver.create_run.assert_called_once()
        assert node_ids == {node.node_id for node in expected_nodes}
        assert updated_node_ids == {node.node_id for node in expected_updated_nodes}

        f_stop.set()
        # Exit
        thread.join()
