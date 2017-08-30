from __future__ import absolute_import

import functools
import operator
import os
import time
import uuid

import pytest
from . import unittest

from kafka.errors import (
    LeaderNotAvailableError, KafkaTimeoutError, InvalidTopicError,
    NotLeaderForPartitionError, UnknownTopicOrPartitionError,
    FailedPayloadsError
)
from test.fixtures import random_string, version_str_to_tuple, version as kafka_version #pylint: disable=wrong-import-order


def kafka_versions(*versions):
    """
    Describe the Kafka versions this test is relevant to.

    The versions are passed in as strings, for example:
        '0.11.0'
        '>=0.10.1.0'
        '>0.9', '<1.0'  # since this accepts multiple versions args

    The current KAFKA_VERSION will be evaluated against this version. If the
    result is False, then the test is skipped. Similarly, if KAFKA_VERSION is
    not set the test is skipped.

    Note: For simplicity, this decorator accepts Kafka versions as strings even
    though the similarly functioning `api_version` only accepts tuples. Trying
    to convert it to tuples quickly gets ugly due to mixing operator strings
    alongside version tuples. While doable when one version is passed in, it
    isn't pretty when multiple versions are passed in.
    """

    def construct_lambda(s):
        if s[0].isdigit():
            op_str = '='
            v_str = s
        elif s[1].isdigit():
            op_str = s[0] # ! < > =
            v_str = s[1:]
        elif s[2].isdigit():
            op_str = s[0:2] # >= <=
            v_str = s[2:]
        else:
            raise ValueError('Unrecognized kafka version / operator: %s' % (s,))

        op_map = {
            '=': operator.eq,
            '!': operator.ne,
            '>': operator.gt,
            '<': operator.lt,
            '>=': operator.ge,
            '<=': operator.le
        }
        op = op_map[op_str]
        version = version_str_to_tuple(v_str)
        return lambda a: op(a, version)

    validators = map(construct_lambda, versions)

    def real_kafka_versions(func):
        @functools.wraps(func)
        def wrapper(func, *args, **kwargs):
            version = kafka_version()

            if not version:
                pytest.skip("no kafka version set in KAFKA_VERSION env var")

            for f in validators:
                if not f(version):
                    pytest.skip("unsupported kafka version")

            return func(*args, **kwargs)
        return wrapper

    return real_kafka_versions


class KafkaIntegrationTestCase(unittest.TestCase):
    create_client = True
    topic = None
    zk = None
    server = None

    def setUp(self):
        super(KafkaIntegrationTestCase, self).setUp()
        if not os.environ.get('KAFKA_VERSION'):
            self.skipTest('Integration test requires KAFKA_VERSION')

        if not self.topic:
            topic = "%s-%s" % (self.id()[self.id().rindex(".") + 1:], random_string(10))
            self.topic = topic

        if self.create_client:
            self.client = SimpleClient('%s:%d' % (self.server.host, self.server.port))

        timeout = time.time() + 30
        while time.time() < timeout:
            try:
                self.client.load_metadata_for_topics(self.topic, ignore_leadernotavailable=False)
                if self.client.has_metadata_for_topic(topic):
                    break
            except (LeaderNotAvailableError, InvalidTopicError):
                time.sleep(1)
        else:
            raise KafkaTimeoutError('Timeout loading topic metadata!')


        # Ensure topic partitions have been created on all brokers to avoid UnknownPartitionErrors
        # TODO: It might be a good idea to move this to self.client.ensure_topic_exists
        for partition in self.client.get_partition_ids_for_topic(self.topic):
            while True:
                try:
                    req = OffsetRequestPayload(self.topic, partition, -1, 100)
                    self.client.send_offset_request([req])
                    break
                except (NotLeaderForPartitionError, UnknownTopicOrPartitionError, FailedPayloadsError) as e:
                    if time.time() > timeout:
                        raise KafkaTimeoutError('Timeout loading topic metadata!')
                    time.sleep(.1)

        self._messages = {}

    def tearDown(self):
        super(KafkaIntegrationTestCase, self).tearDown()
        if not os.environ.get('KAFKA_VERSION'):
            return

        if self.create_client:
            self.client.close()

    def msgs(self, iterable):
        return [self.msg(x) for x in iterable]

    def msg(self, s):
        if s not in self._messages:
            self._messages[s] = '%s-%s-%s' % (s, self.id(), str(uuid.uuid4()))

        return self._messages[s].encode('utf-8')


class Timer(object):
    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.interval = self.end - self.start
