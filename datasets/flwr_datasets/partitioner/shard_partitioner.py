# Copyright 2023 Flower Labs GmbH. All Rights Reserved.
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
"""Shard partitioner class."""


# pylint: disable=R0912
import math
from typing import Dict, List, Optional

import numpy as np

import datasets
from flwr_datasets.partitioner.partitioner import Partitioner


class ShardPartitioner(Partitioner):  # pylint: disable=R0902
    """Partitioner based on shard of (typically) unique classes.

    The algorithm works as follows: the dataset is sorted by label e.g. [samples with
    label 1, samples with labels 2 ...], then the shards are created, with each
    shard of size = `shard_size` if provided or automatically calculated:
    shards_size = len(dataset) / `num_partitions` * `num_shards_per_node`.

    A shard is just a block (chunk) of a `dataset` that contains `shard_size`
    consecutive samples. There might be shards that contain samples associated with more
    than a single unique label. The first case is (remember the preprocessing step sorts
    the dataset by label) when a shard is constructed from samples at the boundaries of
    the sorted dataset and therefore belonging to different classes e.g. the "leftover"
    of samples of class 1 and the majority of class 2. The another scenario when a shard
    has samples with more than one unique label is when the shard size is bigger than
    the  number of samples of a certain class.

    Each partition is created from `num_shards_per_node` that are chosen randomly.

    There are a few ways of partitioning data that result in certain properties
    (depending on the parameters specification):
    1) same number of shards per nodes + the same shard size (specify:
    a) `num_shards_per_nodes`, `shard_size`; or b) `num_shards_per_node`)
    In case of b the `shard_size` is calculated as floor(len(dataset) /
    (`num_shards_per_nodes` * `num_partitions`))
    2) possibly different number of shards per node (use nearly all data) + the same
    shard size (specify: `shard_size` + `keep_incomplete_shard=False`)
    3) possibly different number of shards per node (use all data) + possibly different
    shard size (specify: `shard_size` + `keep_incomplete_shard=True`)


    Algorithm based on the description in Communication-Efficient Learning of Deep
    Networks from Decentralized Data https://arxiv.org/abs/1602.05629. This
    implementation expands on the initial idea by enabling more hyperparameters
    specification therefore providing more control on how partitions are created.
    It enables the division obtained in original paper.

    Parameters
    ----------
    num_partitions : int
        The total number of partitions that the data will be divided into.
    partition_by : str
        Column name of the labels (targets) based on which Dirichlet sampling works.
    num_shards_per_node : Optional[int]
        Number of shards to assign to a single partitioner. It's an alternative to
        `num_partitions`.
    shard_size : Optional[int]
        Size of a single shards (a partition has one or more shards). If the size is not
        given it will be automatically computed.
    keep_incomplete_shard : bool
        Whether to drop the last shard which might be incomplete (smaller than the
        others). If it is dropped each shard is equal size. (It does not mean that each
        client gets equal number of shards, which only happens if
        `num_partitions` % `num_shards` = 0). This parameter has no effect if
        `num_shards_per_nodes` and `shard_size` are specified.
    shuffle: bool
        Whether to randomize the order of samples. Shuffling applied after the
        samples assignment to nodes.
    seed: int
        Seed used for dataset shuffling. It has no effect if `shuffle` is False.

    Examples
    --------
    1) If you need same number of shards per nodes + the same shard size (and you know
    both of these values)
    >>> from flwr_datasets import FederatedDataset
    >>> from flwr_datasets.partitioner import ShardPartitioner
    >>>
    >>> partitioner = ShardPartitioner(num_partitions=10, partition_by="label",
    >>>                                num_shards_per_node=2, shard_size=1_000)
    >>> fds = FederatedDataset(dataset="mnist", partitioners={"train": partitioner})
    >>> partition = fds.load_partition(0)
    >>> print(partition[0])  # Print the first example
    {'image': <PIL.PngImagePlugin.PngImageFile image mode=L size=28x28 at 0x15F616C50>,
    'label': 3}
    >>> partition_sizes = [len(fds.load_partition(node_id)) for node_id in range(10)]
    >>> print(partition_sizes)
    [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]

    2) If you want to use nearly all the data and do not need to have the number of
    shard per each node to be the same
    >>> from flwr_datasets import FederatedDataset
    >>> from flwr_datasets.partitioner import ShardPartitioner
    >>>
    >>> partitioner = ShardPartitioner(num_partitions=9, partition_by="label",
    >>>                                shard_size=1_000)
    >>> fds = FederatedDataset(dataset="mnist", partitioners={"train": partitioner})
    >>> partition_sizes = [len(fds.load_partition(node_id)) for node_id in range(9)]
    >>> print(partition_sizes)
    [7000, 7000, 7000, 7000, 7000, 7000, 6000, 6000, 6000]

    3) If you want to use all the data
        >>> from flwr_datasets import FederatedDataset
    >>> from flwr_datasets.partitioner import ShardPartitioner
    >>>
    >>> partitioner = ShardPartitioner(num_partitions=10, partition_by="label",
    >>>                                shard_size=990, keep_incomplete_shard=True)
    >>> fds = FederatedDataset(dataset="mnist", partitioners={"train": partitioner})
    >>> partition_sizes = [len(fds.load_partition(node_id)) for node_id in range(10)]
    >>> print(sorted(partition_sizes))
    [5550, 5940, 5940, 5940, 5940, 5940, 5940, 5940, 5940, 6930]
    """

    def __init__(  # pylint: disable=R0913
        self,
        num_partitions: int,
        partition_by: str,
        num_shards_per_node: Optional[int] = None,
        shard_size: Optional[int] = None,
        keep_incomplete_shard: bool = False,
        shuffle: bool = True,
        seed: Optional[int] = 42,
    ) -> None:
        super().__init__()
        # Attributes based on the constructor
        _check_if_natual_number(num_partitions, "num_partitions")
        self._num_partitions = num_partitions
        self._partition_by = partition_by
        _check_if_natual_number(num_shards_per_node, "num_shards_per_node", True)
        self._num_shards_per_node = num_shards_per_node
        self._num_shards_used: Optional[int] = None
        _check_if_natual_number(shard_size, "shard_size", True)
        self._shard_size = shard_size
        self._keep_incomplete_shard = keep_incomplete_shard
        self._shuffle = shuffle
        self._seed = seed

        # Utility attributes
        self._rng = np.random.default_rng(seed=self._seed)  # NumPy random generator
        self._node_id_to_indices: Dict[int, List[int]] = {}
        self._node_id_to_indices_determined = False

    def load_partition(self, node_id: int) -> datasets.Dataset:
        """Load a partition based on the partition index.

        Parameters
        ----------
        node_id : int
            the index that corresponds to the requested partition

        Returns
        -------
        dataset_partition : Dataset
            single partition of a dataset
        """
        # The partitioning is done lazily - only when the first partition is
        # requested. Only the first call creates the indices assignments for all the
        # partition indices.
        self._check_num_partitions_correctness_if_needed()
        self._check_possibility_of_partitions_creation()
        self._sort_dataset_if_needed()
        self._determine_node_id_to_indices_if_needed()
        return self.dataset.select(self._node_id_to_indices[node_id])

    def _determine_node_id_to_indices_if_needed(self) -> None:  # pylint: disable=R0914
        """Assign sample indices to each node id.

        This method works on sorted datasets. A "shard" is a part of the dataset of
        consecutive samples (if self._keep_incomplete_shard is False, each shard is same
        size).
        """
        # No need to do anything if that node_id_to_indices are already determined
        if self._node_id_to_indices_determined:
            return

        # One of the specification allows to skip the `num_shards_per_node` param
        if self._num_shards_per_node is not None:
            self._num_shards_used = int(
                self._num_partitions * self._num_shards_per_node
            )
            num_shards_per_node_array = (
                np.ones(self._num_partitions) * self._num_shards_per_node
            )
            if self._shard_size is None:
                self._compute_shard_size_if_missing()
                assert self._shard_size is not None
                if self._keep_incomplete_shard:
                    num_usable_shards_in_dataset = int(
                        math.ceil(len(self.dataset) / self._shard_size)
                    )
                else:
                    num_usable_shards_in_dataset = int(
                        math.floor(len(self.dataset) / self._shard_size)
                    )
            else:
                num_usable_shards_in_dataset = int(
                    math.floor(len(self.dataset) / self._shard_size)
                )
        elif self._num_shards_per_node is None:
            if self._shard_size is None:
                raise ValueError(
                    "The shard_size needs to be specified if the "
                    "num_shards_per_node is None"
                )
            if self._keep_incomplete_shard is False:
                self._num_shards_used = int(
                    math.floor(len(self.dataset) / self._shard_size)
                )
                num_usable_shards_in_dataset = self._num_shards_used
            elif self._keep_incomplete_shard is True:
                self._num_shards_used = int(
                    math.ceil(len(self.dataset) / self._shard_size)
                )
                num_usable_shards_in_dataset = self._num_shards_used
                if num_usable_shards_in_dataset < self._num_partitions:
                    raise ValueError(
                        "Based on the given arguments the creation of the partitions "
                        "is impossible. The implied number of partitions that can be "
                        "used is lower than the number of requested partitions "
                        "resulting in empty partitions. Please decrease the size of "
                        "shards: `shard_size`."
                    )
            else:
                raise ValueError(
                    "The keep_incomplete_shards need to be specified "
                    "when _num_shards_per_node is None."
                )
            num_shards_per_node = int(self._num_shards_used / self._num_partitions)
            # Assign the shards per nodes (so far, the same as in ideal case)
            num_shards_per_node_array = (
                np.ones(self._num_partitions) * num_shards_per_node
            )
            num_shards_assigned = self._num_partitions * num_shards_per_node
            num_shards_to_assign = self._num_shards_used - num_shards_assigned
            # Assign the "missing" shards
            for i in range(num_shards_to_assign):
                num_shards_per_node_array[i] += 1

        else:
            raise ValueError(
                "The specification of nm_shards_per_node and "
                "keep_incomplete_shards is not correct."
            )

        if num_usable_shards_in_dataset < self._num_partitions:
            raise ValueError(
                "The specified configuration results in empty partitions because the "
                "number of usable shards is smaller that the number partitions. "
                "Try decreasing the shard size or the number of partitions. "
            )

        indices_on_which_to_split_shards = np.cumsum(
            num_shards_per_node_array, dtype=int
        )

        shard_indices_array = self._rng.permutation(num_usable_shards_in_dataset)[
            : self._num_shards_used
        ]
        # Randomly assign shards to node_id
        nid_to_shard_indices = np.split(
            shard_indices_array, indices_on_which_to_split_shards
        )[:-1]
        node_id_to_indices: Dict[int, List[int]] = {
            cid: [] for cid in range(self._num_partitions)
        }
        # Compute node_id to sample indices based on the shard indices
        for node_id in range(self._num_partitions):
            for shard_idx in nid_to_shard_indices[node_id]:
                start_id = int(shard_idx * self._shard_size)
                end_id = min(int((shard_idx + 1) * self._shard_size), len(self.dataset))
                node_id_to_indices[node_id].extend(list(range(start_id, end_id)))
        if self._shuffle:
            for indices in node_id_to_indices.values():
                # In place shuffling
                self._rng.shuffle(indices)
        self._node_id_to_indices = node_id_to_indices
        self._node_id_to_indices_determined = True

    def _check_num_partitions_correctness_if_needed(self) -> None:
        """Test num_partitions when the dataset is given (in load_partition)."""
        if not self._node_id_to_indices_determined:
            if self._num_partitions > self.dataset.num_rows:
                raise ValueError(
                    "The number of partitions needs to be smaller than the number of "
                    "samples in the dataset."
                )

    def _sort_dataset_if_needed(self) -> None:
        """Sort dataset prior to determining the partitions.

        Operation only needed to be performed one time. It's required for the creation
        of shards with the same labels.
        """
        if self._node_id_to_indices_determined:
            return
        self._dataset = self.dataset.sort(self._partition_by)

    def _compute_shard_size_if_missing(self) -> None:
        """Compute the parameters needed to perform sharding.

        This method should be called after the dataset is assigned.
        """
        if self._shard_size is None:
            # If shard size is not specified it needs to be computed
            num_rows = self.dataset.num_rows
            self._shard_size = int(num_rows / self._num_shards_used)

    def _check_possibility_of_partitions_creation(self) -> None:
        if self._shard_size is not None and self._num_shards_per_node is not None:
            implied_min_dataset_size = (
                self._shard_size * self._num_shards_per_node * self._num_partitions
            )
            if implied_min_dataset_size > len(self.dataset):
                raise ValueError(
                    f"Based on the given arguments the creation of the "
                    "partitions is impossible. The implied minimum dataset"
                    f"size is {implied_min_dataset_size} but the dataset"
                    f"size is {len(self.dataset)}"
                )


def _check_if_natual_number(
    number: Optional[int], parameter_name: str, none_acceptable: bool = False
) -> None:
    if none_acceptable and number is None:
        return
    if not isinstance(number, int):
        raise TypeError(
            f"The expected type of {parameter_name} is int but given: {number} of type "
            f"{type(number)}. Please specify the correct type."
        )
    if not number >= 1:
        raise ValueError(
            f"The expected value of {parameter_name} is >= 1 (greater or equal to 1) "
            f"but given: {number} which does not meet this condition. Please "
            f"provide a correct number."
        )
