import os
import re
import socket

import pymongo
import botocore.client

import strax
import straxen

export, __all__ = strax.exporter()


default_mongo_url = (
    'mongodb://{username}:{password}@rundbcluster-shard-00-00-cfaei.'
    'gcp.mongodb.net:27017,rundbcluster-shard-00-01-cfaei.gcp.mongodb.net'
    ':27017,rundbcluster-shard-00-02-cfaei.gcp.mongodb.net:27017/test?'
    'ssl=true&replicaSet=RunDBCluster-shard-0&authSource=admin')

@export
class RunDB(strax.StorageFrontend):
    """Frontend that searches RunDB MongoDB for data.

    Loads appropriate backends ranging from Files to S3.
    """

    hosts = {
        'dali': r'^dali.*rcc.*',
    }

    def __init__(self,
                 mongo_url=None,
                 s3_kwargs=None,
                 local_only=False,
                 new_data_path='./strax_data',
                 *args, **kwargs):
        """
        :param mongo_url: URL to Mongo runs database (including auth)
        :param local_only: Do not show data as available if it would have to be
        downloaded from a remote location.
        :param new_data_path: Path where new files are to be written
            defaults to './strax_data'
        :param s3_kwargs: Arguments to initialize S3 backend (including auth)

        Other (kw)args are passed to StorageFrontend.__init__

        TODO: disable S3 if secret keys not known
        """
        super().__init__(*args, **kwargs)
        self.local_only = local_only
        self.new_data_path = new_data_path

        if s3_kwargs is None:
            s3_kwargs = dict(
                aws_access_key_id=straxen.get_secret('s3_access_key_id'),
                aws_secret_access_key=straxen.get_secret('s3_secret_access_key'),      # noqa
                endpoint_url='http://ceph-s3.mwt2.org',
                service_name='s3',
                config=botocore.client.Config(
                    connect_timeout=5,
                    retries=dict(max_attempts=10)))

        if mongo_url is None:
            mongo_url = default_mongo_url.format(
                username=straxen.get_secret('rundb_username'),
                password=straxen.get_secret('rundb_password'))
        self.client = pymongo.MongoClient(mongo_url)
        self.collection = self.client['xenon1t']['runs']

        self.backends = [
            strax.S3Backend(**s3_kwargs),
            strax.FileSytemBackend(),
        ]

        # Construct mongo query for runs with available data.
        # This depends on the machine you're running on.
        self.available_query = []
        if not self.local_only:
            self.available_query.append({'host': 'ceph-s3'})
            
        hostname = socket.getfqdn()
        for host_alias, regex in self.hosts.items():
            if re.match(regex, hostname):
                self.available_query.append({'host': host_alias})

    def _data_query(self, key):
        """Return MongoDB query for data field matching key"""
        return {
            'data': {
                '$elemMatch': {
                    'type': key.data_type,
                    'meta.lineage': key.lineage,
                    '$or': self.available_query}}}

    def _find(self, key: strax.DataKey,
              write, allow_incomplete, fuzzy_for, fuzzy_for_options):
        if fuzzy_for or fuzzy_for_options:
            raise NotImplementedError("Can't do fuzzy with RunDB yet.")

        dq = self._data_query(key)
        doc = self.collection.find_one(
            filter={'name': key.run_id, **dq},
            projection=dq)

        if doc is None:
            # Data was not found
            if write:
                # Return path where we could write it
                return (strax.FileSytemBackend.__name__,
                        os.path.join(self.new_data_path, str(key)))
            raise strax.DataNotAvailable

        datum = doc['data'][0]

        if write and not self._can_overwrite(key):
            raise strax.DataExistsError(at=datum['protocol'])

        return datum['protocol'], datum['location']

    def _list_available(self, key: strax.DataKey,
              allow_incomplete, fuzzy_for, fuzzy_for_options):
        if fuzzy_for or fuzzy_for_options:
            raise NotImplementedError("Can't do fuzzy with RunDB yet.")

        dq = self._data_query(key)
        cursor = self.collection.find(dq, projection=['name'])
        return [x['name'] for x in cursor]

    def run_metadata(self, run_id, projection=None):
        doc = self.collection.find_one({'name': run_id}, projection=projection)
        if doc is None:
            raise strax.DataNotAvailable
        return doc
