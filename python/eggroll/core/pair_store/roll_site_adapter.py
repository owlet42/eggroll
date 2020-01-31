#
#  Copyright 2019 The Eggroll Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import sys

from eggroll.core.error import GrpcCallError
from eggroll.core.grpc.factory import GrpcChannelFactory
from eggroll.core.meta_model import ErEndpoint
from eggroll.core.pair_store.adapter import PairWriteBatch, PairIterator, \
    PairAdapter
from eggroll.core.pair_store.format import PairBinWriter, ArrayByteBuffer
from eggroll.core.proto import meta_pb2
from eggroll.core.proto import proxy_pb2, proxy_pb2_grpc
from eggroll.core.serdes import eggroll_serdes
from eggroll.core.utils import _elements_to_proto
from eggroll.utils.log_utils import get_logger

L = get_logger()

_serdes = eggroll_serdes.PickleSerdes
OBJECT_STORAGE_NAME = "__federation__"
DELIM = "#"


class RollSiteWriteBatch(PairWriteBatch):
    grpc_channel_factory = GrpcChannelFactory()

    # TODO:0: check if secure channel needed
    def __init__(self, adapter, options={}):
        self.adapter = adapter
        self.name = DELIM.join([OBJECT_STORAGE_NAME,
                                adapter.job_id,
                                adapter.name,
                                adapter.tag,
                                adapter.src_role,
                                adapter.src_party_id,
                                adapter.dst_role,
                                adapter.dst_party_id])

        self.namespace = adapter.namespace
        self.src_role = adapter.src_role
        self.src_party_id = adapter.src_party_id
        self.dst_role = adapter.dst_role
        self.dst_party_id = adapter.dst_party_id
        self.obj_type = adapter.obj_type
        self.tagged_key = ''

        self.proxy_endpoint = ErEndpoint(host=adapter._dst_host, port=adapter._dst_port)
        channel = self.grpc_channel_factory.create_channel(self.proxy_endpoint)
        self.stub = proxy_pb2_grpc.DataTransferServiceStub(channel)

        self.__bin_packet_len = 16 << 20
        self.total_written = 0

        self.ba = bytearray(self.__bin_packet_len)
        self.buffer = ArrayByteBuffer(self.ba)
        self.writer = PairBinWriter(pair_buffer=self.buffer)

    def generate_message(self, obj, metadata):
        while True:
            data = proxy_pb2.Data(value=obj)
            metadata.seq += 1
            packet = proxy_pb2.Packet(header=metadata, body=data)
            yield packet
            break

    # TODO:0: configurable
    def push(self, obj):
        task_info = proxy_pb2.Task(taskId=self.name, model=proxy_pb2.Model(name=self.name, dataKey=self.namespace))
        topic_src = proxy_pb2.Topic(name=self.name, partyId="{}".format(self.src_party_id),
                                    role=self.src_role, callback=None)
        topic_dst = proxy_pb2.Topic(name=self.name, partyId="{}".format(self.dst_party_id),
                                    role=self.dst_role, callback=None)
        command_test = proxy_pb2.Command()

        # TODO: conf test as config and use it
        conf_test = proxy_pb2.Conf(overallTimeout=200000,
                                   completionWaitTimeout=200000,
                                   packetIntervalTimeout=200000,
                                   maxRetries=10)

        metadata = proxy_pb2.Metadata(task=task_info,
                                      src=topic_src,
                                      dst=topic_dst,
                                      command=command_test,
                                      seq=0,
                                      ack=0)

        try:
            self.stub.push(self.generate_message(obj, metadata))
        except Exception as e:
            raise GrpcCallError("push", self.proxy_endpoint, e)

    def write(self, bin_data):
        self.push(bin_data)

    def send_end(self):
        print("send_end tagged_key:", self.tagged_key)
        task_info = proxy_pb2.Task(taskId=self.name, model=proxy_pb2.Model(name=self.obj_type, dataKey=self.tagged_key))
        topic_src = proxy_pb2.Topic(name="set_status", partyId="{}".format(self.src_party_id),
                                    role=self.src_role, callback=None)
        topic_dst = proxy_pb2.Topic(name="set_status", partyId=self.dst_party_id,
                                    role=self.dst_role, callback=None)
        command_test = proxy_pb2.Command(name="set_status")
        conf_test = proxy_pb2.Conf(overallTimeout=20000,
                                   completionWaitTimeout=20000,
                                   packetIntervalTimeout=20000,
                                   maxRetries=10)

        metadata = proxy_pb2.Metadata(task=task_info,
                                      src=topic_src,
                                      dst=topic_dst,
                                      command=command_test,
                                      operator="markEnd",
                                      seq=0,
                                      ack=0)

        packet = proxy_pb2.Packet(header=metadata)

        try:
            # TODO:0: retry and sleep for all grpc call in RollSite
            self.stub.unaryCall(packet)
        except Exception as e:
            raise GrpcCallError('send_end', self.proxy_endpoint, e)

    def close(self):
        bin_batch = bytes(self.ba[0:self.buffer.get_offset()])
        self.write(bin_batch)
        self.send_end()

    def put(self, k, v):
        print("self.type:", self.obj_type)
        print("type:", type(self.obj_type))
        print("k:", k)
        if self.obj_type == 'object':
            print("set tagged_key:", k)
            self.tagged_key = _serdes.deserialize(k)

        try:
            self.writer.write(k, v)
        except IndexError as e:
            bin_batch = bytes(self.ba[0:self.buffer.get_offset()])
            self.write(bin_batch)
            # TODO:0: replace 1024 with constant
            self.ba = bytearray(max(self.__bin_packet_len, len(k) + len(v) + 1024))
            self.buffer = ArrayByteBuffer(self.ba)
            self.writer = PairBinWriter(pair_buffer=self.buffer)
            self.writer.write(k, v)
        except:
            print("Unexpected error:", sys.exc_info()[0])
            raise


class RollSiteIterator(PairIterator):
    def __init__(self, adapter):
        self.adapter = adapter
        self.it = adapter.db.iteritems()
        self.it.seek_to_first()

    def first(self):
        print("first called")
        count = 0
        self.it.seek_to_first()
        for k, v in self.it:
            count += 1
        self.it.seek_to_first()
        return (count != 0)

    def last(self):
        count = 0
        self.it.seek_to_last()
        for k, v in self.it:
            count += 1
        self.it.seek_to_last()
        return (count != 0)

    def key(self):
        return self.it.get()[0]

    def close(self):
        pass

    def __iter__(self):
        return self.it


class RollSiteAdapter(PairAdapter):
    def __init__(self, options):
        super().__init__(options)
        name = options["path"].split("/")[-2]
        print("self._name:", name)
        self.namespace = options["path"].split("/")[-3]
        args = name.split(DELIM, 11)  #args[8]='9394/0'
        print(args)

        self.job_id = args[1]
        self.name = args[2]
        self.tag = args[3]
        self.src_role = args[4]
        self.src_party_id = args[5]
        self.dst_role = args[6]
        self.dst_party_id = args[7]
        self._dst_host = args[8]
        self._dst_port = int(args[9])
        self.obj_type = args[10]          #obj or rollpair

        er_partition = options['er_partition']
        store_locator = er_partition._store_locator

        _store_type = 'roll_site'
        self._store_locator = meta_pb2.StoreLocator(storeType=_store_type,
                                                    namespace=self.namespace,
                                                    name=name,
                                                    partitioner=store_locator._partitioner,
                                                    serdes=store_locator._serdes,
                                                    totalPartitions=store_locator._total_partitions)

    def to_proto(self):
        return meta_pb2.Store(storeLocator=self._store_locator,
                              partitions=_elements_to_proto(self._partitions))

    def close(self):
        pass

    def iteritems(self):
        return RollSiteIterator(self)

    def new_batch(self):
        return RollSiteWriteBatch(self)

    def get(self, key):
        pass

    def put(self, key, value):
        pass
