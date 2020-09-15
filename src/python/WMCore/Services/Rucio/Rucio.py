#!/usr/bin/env python
# coding=utf-8
"""
Rucio Service class developed on top of the native Rucio Client
APIs providing custom output and handling as necessary for the
CMS Workload Management system (and migration from PhEDEx).
"""
from __future__ import division, print_function, absolute_import

import logging
import random
from copy import deepcopy
from rucio.client import Client
from rucio.common.exception import (AccountNotFound, DataIdentifierNotFound, AccessDenied, DuplicateRule,
                                    DataIdentifierAlreadyExists, DuplicateContent, InvalidRSEExpression,
                                    UnsupportedOperation, FileAlreadyExists, RuleNotFound)
from Utils.MemoryCache import MemoryCache
from WMCore.WMException import WMException

RUCIO_VALID_PROJECT = ("Production", "RelVal", "Tier0", "Test", "User")


class WMRucioException(WMException):
    """
    _WMRucioException_
    Generic WMCore exception for Rucio
    """
    pass


def validateMetaData(did, metaDict, logger):
    """
    This function can be extended in the future, for now it will only
    validate the DID creation metadata, more specifically only the
    "project" parameter
    :param did: the DID that will be inserted
    :param metaDict: a dictionary with all the DID metadata data to be inserted
    :param logger: a logger object
    :return: False if validation fails, otherwise True
    """
    if metaDict.get("project", "Production") in RUCIO_VALID_PROJECT:
        return True
    msg = "DID: %s has an invalid 'project' meta-data value: %s" % (did, metaDict['project'])
    msg += "The supported 'project' values are: %s" % str(RUCIO_VALID_PROJECT)
    logger.error(msg)
    return False


def weightedChoice(choices):
    # from https://stackoverflow.com/questions/3679694/a-weighted-version-of-random-choice
    # Python 3.6 includes something like this in the random library itself

    total = sum(w for c, w in choices)
    r = random.uniform(0, total)
    upto = 0
    for c, w in choices:
        if upto + w >= r:
            return c
        upto += w
    assert False, "Shouldn't get here"


class Rucio(object):
    """
    Service class providing additional Rucio functionality on top of the
    Rucio client APIs.

    Just a reminder, we have different naming convention between Rucio
    and CMS, where:
     * CMS dataset corresponds to a Rucio container
     * CMS block corresponds to a Rucio dataset
     * CMS file corresponds to a Rucio file
    We will try to use Container -> CMS dataset, block -> CMS block.
    """

    def __init__(self, acct, hostUrl=None, authUrl=None, configDict=None):
        """
        Constructs a Rucio object with the Client object embedded.
        In order to instantiate a Rucio Client object, it assumes the host has
        a proper rucio configuration file, where the default host and authentication
        host URL come from, as well as the X509 certificate information.
        :param acct: rucio account to be used
        :param hostUrl: defaults to the rucio config one
        :param authUrl: defaults to the rucio config one
        :param configDict: dictionary with extra parameters
        """
        configDict = configDict or {}
        # default RSE data caching to 12h
        rseCacheExpiration = configDict.pop('cacheExpiration', 12 * 60 * 60)
        self.logger = configDict.pop("logger", logging.getLogger())

        self.rucioParams = deepcopy(configDict)
        self.rucioParams.setdefault('account', acct)
        self.rucioParams.setdefault('rucio_host', hostUrl)
        self.rucioParams.setdefault('auth_host', authUrl)
        self.rucioParams.setdefault('ca_cert', None)
        self.rucioParams.setdefault('auth_type', None)
        self.rucioParams.setdefault('creds', None)
        self.rucioParams.setdefault('timeout', 600)
        self.rucioParams.setdefault('user_agent', 'wmcore-client')

        # yield output compatible with the PhEDEx service class
        self.phedexCompat = self.rucioParams.get("phedexCompatible", True)

        self.logger.info("WMCore Rucio initialization parameters: %s", self.rucioParams)
        self.cli = Client(rucio_host=hostUrl, auth_host=authUrl, account=acct,
                          ca_cert=self.rucioParams['ca_cert'], auth_type=self.rucioParams['auth_type'],
                          creds=self.rucioParams['creds'], timeout=self.rucioParams['timeout'],
                          user_agent=self.rucioParams['user_agent'])
        clientParams = {}
        for k in ("host", "auth_host", "auth_type", "account", "user_agent",
                  "ca_cert", "creds", "timeout", "request_retries"):
            clientParams[k] = getattr(self.cli, k)
        self.logger.info("Rucio client initialization parameters: %s", clientParams)

        # keep a map of rse expression to RSE names mapped for some time
        self.cachedRSEs = MemoryCache(rseCacheExpiration, {})

    def pingServer(self):
        """
        _pingServer_

        Ping the rucio server to see whether it's alive
        :return: a dict with the server version
        """
        return self.cli.ping()

    def whoAmI(self):
        """
        _whoAmI_

        Get information about account whose token is used
        :return: a dict with the account information. None in case of failure
        """
        return self.cli.whoami()

    def getAccount(self, acct):
        """
        _getAccount_

        Gets information about a specific account
        :return: a dict with the account information. None in case of failure
        """
        res = None
        try:
            res = self.cli.get_account(acct)
        except (AccountNotFound, AccessDenied) as ex:
            self.logger.error("Failed to get account information from Rucio. Error: %s", str(ex))
        return res

    def getAccountLimits(self, acct):
        """
        Provided an account name, fetch the storage quota for all RSEs
        :param acct: a string with the rucio account name
        :return: a dictionary of RSE name and quota in bytes.
        """
        res = {}
        try:
            res = self.cli.get_local_account_limits(acct)
        except AccountNotFound as ex:
            self.logger.error("Account: %s not found in the Rucio Server. Error: %s", acct, str(ex))
        return res

    def getAccountUsage(self, acct, rse=None):
        """
        _getAccountUsage_

        Provided an account name, gets the storage usage for it against
        a given RSE (or all RSEs)
        :param acct: a string with the rucio account name
        :param rse: an optional string with the RSE name
        :return: a list of dictionaries with the account usage information.
          None in case of failure
        """
        res = None
        try:
            res = list(self.cli.get_local_account_usage(acct, rse=rse))
        except (AccountNotFound, AccessDenied) as ex:
            self.logger.error("Failed to get account usage information from Rucio. Error: %s", str(ex))
        return res

    def getBlocksInContainer(self, container, scope='cms'):
        """
        _getBlocksInContainer_

        Provided a Rucio container - CMS dataset - retrieve all blocks in it.
        :param container: a CMS dataset string
        :param scope: string containing the Rucio scope (defaults to 'cms')
        :return: a list of block names
        """
        blockNames = []
        if not self.isContainer(container):
            # input container wasn't really a container
            self.logger.warning("Provided DID name is not a CONTAINER type: %s", container)
            return blockNames

        response = self.cli.list_content(scope=scope, name=container)
        for item in response:
            if item['type'].upper() == 'DATASET':
                blockNames.append(item['name'])

        return blockNames

    def getReplicaInfoForBlocks(self, **kwargs):
        """
        _getReplicaInfoForBlocks_

        Get block replica information.
        It mimics the same API available in the PhEDEx Service module.

        kwargs originally available for PhEDEx are:
        - dataset       dataset name, can be multiple (*)
        - block         block name, can be multiple (*)
        - node          node name, can be multiple (*)
        - se            storage element name, can be multiple (*)
        - update_since  unix timestamp, only return replicas updated since this time
        - create_since  unix timestamp, only return replicas created since this time
        - complete      y or n, whether or not to require complete or incomplete blocks.
                        Default is to return either
        - subscribed    y or n, filter for subscription. default is to return either.
        - custodial     y or n. filter for custodial responsibility.
                        Default is to return either.
        - group         group name. Default is to return replicas for any group.

        kwargs supported by Rucio are:
        - dids             The list of data identifiers (DIDs) like : [{'scope': <scope1>, 'name': <name1>},
                           {'scope': <scope2>, 'name': <name2>}, ...]
        - schemes          A list of schemes to filter the replicas. (e.g. file, http, ...)
        - unavailable      Also include unavailable replicas in the list. Default to False
        - metalink         False (default) retrieves as JSON, True retrieves as metalink4+xml.
        - rse_expression   The RSE expression to restrict replicas on a set of RSEs.
        - client_location  Client location dictionary for PFN modification {'ip', 'fqdn', 'site'}
        - sort             Sort the replicas:
                           geoip - based on src/dst IP topographical distance
                           closeness - based on src/dst closeness
                           dynamic - Rucio Dynamic Smart Sort (tm)
        - domain           Define the domain. None is fallback to 'wan', otherwise 'wan', 'lan', or 'all'
        - resolve_archives When set to True, find archives which contain the replicas.
        - resolve_parents  When set to True, find all parent datasets which contain the replicas.

        :kwargs: either a dataset or a block name has to be provided. Not both!
        :return: a list of dictionaries with replica information; or a dictionary
        compatible with PhEDEx.
        """
        kwargs.setdefault("scope", "cms")

        blockNames = []
        result = []

        if isinstance(kwargs.get('block', None), (list, set)):
            blockNames = kwargs['block']
        elif 'block' in kwargs:
            blockNames = [kwargs['block']]

        if isinstance(kwargs.get('dataset', None), (list, set)):
            for datasetName in kwargs['dataset']:
                blockNames.extend(self.getBlocksInContainer(datasetName, scope=kwargs['scope']))
        elif 'dataset' in kwargs:
            blockNames.extend(self.getBlocksInContainer(kwargs['dataset'], scope=kwargs['scope']))

        inputDids = []
        for block in blockNames:
            inputDids.append({"scope": kwargs["scope"], "type": "DATASET", "name": block})

        resultDict = {}
        for item in self.cli.list_dataset_replicas_bulk(inputDids):
            resultDict.setdefault(item['name'], [])
            if item['state'].upper() == 'AVAILABLE':
                resultDict[item['name']].append(item['rse'])

        if self.phedexCompat:
            # then we need to convert it to a format like:
            # {"phedex": {"block": [{"name": "block_A", "replica": [{"node": "nodeA"}, {"node": "nodeB"}]},
            #                        etc etc
            #                        }}
            for blockName, rses in resultDict.viewitems():
                replicas = [{"node": rse} for rse in rses]
                result.append({"name": blockName, "replica": replicas})
            result = {'phedex': {'block': result}}
        else:
            # then a list of dictionaries sounds right, e.g.:
            # [{"name": "block_A", "replica": ["nodeA", "nodeB"]},
            #  {"name": "block_B", etc etc}]
            for blockName, rses in resultDict.viewitems():
                result.append({"name": blockName, "replica": list(set(rses))})

        return result

    def getPFN(self, nodes=None, lfns=None, destination=None, protocol='srmv2', custodial='n'):
        """
        Copy of the method from the PhEDEx service class
        Used by CRABServer
        """
        raise NotImplementedError("Apparently not available in the Rucio client as well")

    def createContainer(self, name, scope='cms', **kwargs):
        """
        _createContainer_

        Create a CMS dataset (Rucio container) in a given scope.
        :param name: string with the container name
        :param scope: optional string with the scope name
        :param kwargs:  supported keyword arguments (from the Rucio CLI API documentation):
           * statuses:  Dictionary with statuses, e.g.g {"monotonic":True}.
           * meta:      Meta-data associated with the data identifier is represented using key/value
                        pairs in a dictionary.
           * rules:     Replication rules associated with the data identifier. A list of dictionaries,
                        e.g., [{"copies": 2, "rse_expression": "TIERS1"}, ].
           * lifetime:  DID's lifetime (in seconds).
        :return: a boolean to represent whether it succeeded or not
        """
        response = False
        if not validateMetaData(name, kwargs.get("meta", {}), logger=self.logger):
            return response
        try:
            # add_container(scope, name, statuses=None, meta=None, rules=None, lifetime=None)
            response = self.cli.add_container(scope, name, **kwargs)
        except DataIdentifierAlreadyExists:
            self.logger.debug("Container name already exists in Rucio: %s", name)
            response = True
        except Exception as ex:
            self.logger.error("Exception creating container: %s. Error: %s", name, str(ex))
        return response

    def createBlock(self, name, scope='cms', attach=True, **kwargs):
        """
        _createBlock_

        Create a CMS block (Rucio dataset) in a given scope. It also associates
        the block to its container
        :param name: string with the block name
        :param scope: optional string with the scope name
        :param attach: boolean whether to attack this block to its container or not
        :param kwargs:  supported keyword arguments (from the Rucio CLI API documentation):
           * statuses:  Dictionary with statuses, e.g. {"monotonic":True}.
           * lifetime:  DID's lifetime (in seconds).
           * files:     The content.
           * rse:       The RSE name when registering replicas.
           * meta:      Meta-data associated with the data identifier. Represented
                        using key/value pairs in a dictionary.
           * rules:     replication rules associated with the data identifier. A list of
                        dictionaries, e.g., [{"copies": 2, "rse_expression": "TIERS1"}, ].
        :return: a boolean with the outcome of the operation

        NOTE: we will very likely define a rule saying: keep this block at the location it's
              being produced
        """
        response = False
        if not validateMetaData(name, kwargs.get("meta", {}), logger=self.logger):
            return response
        try:
            # add_dataset(scope, name, statuses=None, meta=None, rules=None, lifetime=None, files=None, rse=None)
            response = self.cli.add_dataset(scope, name, **kwargs)
        except DataIdentifierAlreadyExists:
            self.logger.debug("Block name already exists in Rucio: %s", name)
            response = True
        except Exception as ex:
            self.logger.error("Exception creating block: %s. Error: %s", name, str(ex))

        # then attach this block recently created to its container
        if response and attach:
            container = name.split('#')[0]
            response = self.attachDIDs(kwargs.get('rse'), container, name, scope)
        return response

    def attachDIDs(self, rse, superDID, dids, scope='cms'):
        """
        _attachDIDs_

        Create a list of files - in bulk - to a RSE. Then attach them to the block name.
        :param rse: string with the RSE name
        :param superDID: upper structure level (can be a container or block. If attaching blocks,
             then it's a container name; if attaching files, then it's a block name)
        :param dids: either a string or a list of data identifiers (can be block or files)
        :param scope: string with the scope name
        :return: a boolean to represent whether it succeeded or not
        """
        if not isinstance(dids, list):
            dids = [dids]
        dids = [{'scope': scope, 'name': did} for did in dids]

        response = False
        try:
            response = self.cli.attach_dids(scope, superDID, dids=dids, rse=rse)
        except DuplicateContent:
            self.logger.warning("Dids: %s already attached to: %s", dids, superDID)
            response = True
        except FileAlreadyExists:
            self.logger.warning("Failed to attach files already existent on block: %s", superDID)
            response = True
        except DataIdentifierNotFound:
            self.logger.error("Failed to attach dids: %s. Parent DID %s does not exist.", dids, superDID)
        except Exception as ex:
            self.logger.error("Exception attaching dids: %s to: %s. Error: %s",
                              dids, superDID, str(ex))
        return response

    def createReplicas(self, rse, files, block, scope='cms', ignoreAvailability=True):
        """
        _createReplicas_

        Create a list of files - in bulk - to a RSE. Then attach them to the block name.
        :param rse: string with the RSE name
        :param files: list of dictionaries with the file names and some of its meta data.
            E.g.: {'name': lfn, 'bytes': size, 'scope': scope, 'adler32': checksum, 'state': 'A'}
            State 'A' means that the replica is available
        :param block: string with the block name
        :param scope: string with the scope name
        :param ignore_availability: boolean to ignore the RSE blacklisting
        :return: a boolean to represent whether it succeeded or not
        """
        if isinstance(files, dict):
            files = [files]
        for item in files:
            item['scope'] = scope

        response = False
        try:
            # add_replicas(rse, files, ignore_availability=True)
            response = self.cli.add_replicas(rse, files, ignoreAvailability)
        except Exception as ex:
            self.logger.error("Failed to add replicas for: %s and block: %s. Error: %s", files, block, str(ex))

        if response:
            files = [item['name'] for item in files]
            response = self.attachDIDs(rse, block, files, scope)

        return response

    def closeBlockContainer(self, name, scope='cms'):
        """
        _closeBlockContainer_

        Method to close a block or container, such that it doesn't get any more
        blocks and/or files inserted.
        :param name: data identifier (either a block or a container name)
        :param scope: string with the scope name
        :return: a boolean to represent whether it succeeded or not
        """
        response = False
        try:
            response = self.cli.close(scope, name)
        except UnsupportedOperation:
            self.logger.warning("Container/block has been closed already: %s", name)
            response = True
        except DataIdentifierNotFound:
            self.logger.error("Failed to close DID: %s; it does not exist.", name)
        except Exception as ex:
            self.logger.error("Exception closing container/block: %s. Error: %s", name, str(ex))
        return response

    def createReplicationRule(self, names, rseExpression, scope='cms', copies=1, **kwargs):
        """
        _createReplicationRule_

        Creates a replication rule against a list of data identifiers.
        :param names: either a string with a did or a list of dids of the same type
          (either a block or a container name)
        :param rseExpression: boolean string expression to give the list of RSEs.
           Full documentation on: https://rucio.readthedocs.io/en/latest/rse_expressions.html
           E.g.: "tier=2&US=true"
        :param scope: string with the scope name
        :param kwargs:  supported keyword arguments (from the Rucio CLI API documentation):
           * weight:    If the weighting option of the replication rule is used,
             the choice of RSEs takes their weight into account.
           * lifetime:  The lifetime of the replication rules (in seconds).
           * grouping:  Decides how the replication is going to happen; where:
                ALL: All files will be replicated to the same RSE.
                DATASET: All files in the same block will be replicated to the same RSE.
                NONE: Files will be completely spread over all allowed RSEs without any
                grouping considerations at all.
                E.g.: ALL grouping for 3 blocks against 3 RSEs. All 3 blocks go to one of the RSEs
                      DATASET grouping for 3 blocks against 3 RSEs, each block gets fully replicated
                      to one of those random RSEs
           * account:   The account owning the rule.
           * locked:    If the rule is locked, it cannot be deleted.
           * source_replica_expression: RSE Expression for RSEs to be considered for source replicas.
           * activity:  Transfer Activity to be passed to FTS.
           * notify:    Notification setting for the rule (Y, N, C).
           * purge_replicas: When the rule gets deleted purge the associated replicas immediately.
           * ignore_availability: Option to ignore the availability of RSEs.
           * ask_approval: Ask for approval of this replication rule.
           * asynchronous: Create rule asynchronously by judge-injector.
           * priority:  Priority of the transfers.
           * comment:   Comment about the rule.
           * meta:      Metadata, as dictionary.
        :return: it returns either an empty list or a list with a string id for the rule created

        NOTE: if there is an AccessDenied rucio exception, it raises a WMRucioException
        """
        kwargs.setdefault('grouping', 'ALL')
        kwargs.setdefault('account', self.rucioParams.get('account'))
        kwargs.setdefault('lifetime', None)
        kwargs.setdefault('locked', False)
        kwargs.setdefault('notify', 'N')
        kwargs.setdefault('purge_replicas', False)
        kwargs.setdefault('ignore_availability', False)
        kwargs.setdefault('ask_approval', False)
        kwargs.setdefault('asynchronous', True)
        kwargs.setdefault('priority', 3)

        if not isinstance(names, (list, set)):
            names = [names]
        dids = [{'scope': scope, 'name': did} for did in names]

        response = []
        try:
            response = self.cli.add_replication_rule(dids, copies, rseExpression, **kwargs)
        except AccessDenied as ex:
            msg = "AccessDenied creating DID replication rule. Error: %s" % str(ex)
            raise WMRucioException(msg)
        except DuplicateRule as ex:
            # NOTE:
            #    The unique constraint is per did and it is checked against the tuple:
            #    rucioAccount + did = (name, scope) + rseExpression
            # NOTE:
            #    This exception will be thrown by Rucio even if a single Did has
            #    a duplicate rule. In this case all the rest of the Dids will be
            #    ignored, which in general should be addressed by Rucio. But since
            #    it is not, we should break the list of Dids and proceed one by one
            self.logger.warning("Resolving duplicate rules and separating every DID in a new rule...")

            ruleIds = []
            # now try creating a new rule for every single DID in a separated call
            for did in dids:
                try:
                    response = self.cli.add_replication_rule([did], copies, rseExpression, **kwargs)
                    ruleIds.extend(response)
                except DuplicateRule:
                    self.logger.warning("Found duplicate rule for account: %s\n, rseExp: %s\ndids: %s",
                                        kwargs['account'], rseExpression, dids)
                    # Well, then let us find which rule_id is already in the system
                    for rule in self.listDataRules(did['name'], did['scope']):
                        if rule['account'] == kwargs['account'] and rule['rse_expression'] == rseExpression:
                            # then this is the duplicate rule!
                            ruleIds.append(rule['id'])
            return list(set(ruleIds))
        except Exception as ex:
            self.logger.error("Exception creating rule replica for data: %s. Error: %s", names, str(ex))
        return response

    def listRuleHistory(self, dids):
        """
        _listRuleHistory_

        A function to return a list of historical records of replication rules
        per did.
        :param dids: a list of dids of the form {'name': '...', 'scope: '...'}

        The returned structure looks something like:
        [{'did': {'name': 'DidName',
                  'scope': 'cms'},
          'did_hist': [{u'account': u'wma_test',
                        u'created_at': datetime.datetime(2020, 6, 30, 1, 34, 51),
                        u'locks_ok_cnt': 9,
                        u'locks_replicating_cnt': 0,
                        u'locks_stuck_cnt': 0,
                        u'rse_expression': u'(tier=2|tier=1)&cms_type=real&rse_type=DISK',
                        u'rule_id': u'1f0ab297e4b54e1abf7c086ac012b9e9',
                        u'state': u'OK',
                        u'updated_at': datetime.datetime(2020, 6, 30, 1, 34, 51)}]},
         ...
         {'did': {},
          'did_hist': []}]
        """
        fullHistory = []
        for did in dids:
            didHistory = {}
            didHistory['did'] = did
            didHistory['did_hist'] = []
            # check the full history of the current did
            for hist in self.cli.list_replication_rule_full_history(did['scope'], did['name']):
                didHistory['did_hist'].append(hist)
            fullHistory.append(didHistory)
        return fullHistory

    def listContent(self, name, scope='cms'):
        """
        _listContent_

        List the content of the data identifier, returning some very basic information.
        :param name: data identifier (either a block or a container name)
        :param scope: string with the scope name
        :return: a list with dictionary items
        """
        res = []
        try:
            res = self.cli.list_content(scope, name)
        except Exception as ex:
            self.logger.error("Exception listing content of: %s. Error: %s", name, str(ex))
        return list(res)

    def listDataRules(self, name, scope='cms'):
        """
        _listDataRules_

        List all rules associated to the data identifier provided.
        :param name: data identifier (either a block or a container name)
        :param scope: string with the scope name
        :return: a list with dictionary items
        """
        res = []
        try:
            res = self.cli.list_did_rules(scope, name)
        except Exception as ex:
            self.logger.error("Exception listing rules for data: %s. Error: %s", name, str(ex))
        return list(res)

    def listDataRulesHistory(self, name, scope='cms'):
        """
        _listDataRulesHistory_

        List the whole rule history of a given DID.
        :param name: data identifier (either a block or a container name)
        :param scope: string with the scope name
        :return: a list with dictionary items
        """
        res = []
        try:
            res = self.cli.list_replication_rule_full_history(scope, name)
        except Exception as ex:
            self.logger.error("Exception listing rules history for data: %s. Error: %s", name, str(ex))
        return list(res)

    def getRule(self, ruleId, estimatedTtc=False):
        """
        _getRule_

        Retrieve rule information for a given rule id
        :param ruleId: string with the rule id
        :param estimatedTtc: bool, if rule_info should return ttc information
        :return: a dictionary with the rule data (or empty if no rule can be found)
        """
        res = {}
        try:
            res = self.cli.get_replication_rule(ruleId, estimate_ttc=estimatedTtc)
        except RuleNotFound:
            self.logger.error("Cannot find any information for rule id: %s", ruleId)
        except Exception as ex:
            self.logger.error("Exception getting rule id: %s. Error: %s", ruleId, str(ex))
        return res

    def deleteRule(self, ruleId, purgeReplicas=False):
        """
        _deleteRule_

        Deletes a replication rule and all its associated locks
        :param ruleId: string with the rule id
        :param purgeReplicas: bool,ean to immediately delete the replicas
        :return: a boolean to represent whether it succeeded or not
        """
        res = True
        try:
            res = self.cli.delete_replication_rule(ruleId, purge_replicas=purgeReplicas)
        except RuleNotFound:
            self.logger.error("Could not find rule id: %s. Assuming it has already been deleted", ruleId)
        except Exception as ex:
            self.logger.error("Exception deleting rule id: %s. Error: %s", ruleId, str(ex))
            res = False
        return res

    def evaluateRSEExpression(self, rseExpr, useCache=True):
        """
        Provided an RSE expression, resolve it and return a flat list of RSEs
        :param rseExpr: an RSE expression (which could be the RSE itself...)
        :param useCache: boolean defining whether cached data is meant to be used or not
        :return: a list of RSE names
        """
        if self.cachedRSEs.isCacheExpired():
            self.cachedRSEs.reset()
        if useCache and rseExpr in self.cachedRSEs:
            return self.cachedRSEs[rseExpr]
        else:
            matchingRSEs = []
            try:
                for item in self.cli.list_rses(rseExpr):
                    matchingRSEs.append(item['rse'])
            except InvalidRSEExpression as exc:
                msg = "Provided RSE expression is considered invalid: {}. Error: {}".format(rseExpr, str(exc))
                raise WMRucioException(msg)
        # add this key/value pair to the cache
        self.cachedRSEs.addItemToCache({rseExpr: matchingRSEs})
        return matchingRSEs

    def pickRSE(self, rseExpression='rse_type=TAPE\cms_type=test', rseAttribute='ddm_quota', minNeeded=0):
        """
        _pickRSE_

        Use a weighted random selection algorithm to pick an RSE for a dataset based on an attribute
        The attribute should correlate to space available.
        :param rseExpression: Rucio RSE expression to pick RSEs (defaults to production Tape RSEs)
        :param rseAttribute: The RSE attribute to use as a weight. Must be a number
        :param minNeeded: If the RSE attribute is less than this number, the RSE will not be considered.

        Returns: A tuple of the chosen RSE and if the chosen RSE requires approval to write (rule property)
        """
        matchingRSEs = self.evaluateRSEExpression(rseExpression)
        rsesWithWeights = []

        for rse in matchingRSEs:
            attrs = self.cli.list_rse_attributes(rse)
            if rseAttribute:
                try:
                    quota = float(attrs.get(rseAttribute, 0))
                except (TypeError, KeyError):
                    quota = 0
            else:
                quota = 1
            requiresApproval = attrs.get('requires_approval', False)
            if quota > minNeeded:
                rsesWithWeights.append(((rse, requiresApproval), quota))

        choice = weightedChoice(rsesWithWeights)
        return choice

    def isContainer(self, didName, scope='cms'):
        """
        Checks whether the DID name corresponds to a container type or not.
        :param didName: string with the DID name
        :param scope: string containing the Rucio scope (defaults to 'cms')
        :return: True if the DID is a container, else False
        """
        try:
            response = self.cli.get_did(scope=scope, name=didName)
        except DataIdentifierNotFound as exc:
            msg = "Data identifier not found in Rucio: {}. Error: {}".format(didName, str(exc))
            raise WMRucioException(msg)
        return response['type'].upper() == 'CONTAINER'

    def getDID(self, didName, dynamic=True, scope='cms'):
        """
        Retrieves basic information for a single data identifier.
        :param didName: string with the DID name
        :param dynamic: boolean to dynamically calculate the DID size (default to True)
        :param scope: string containing the Rucio scope (defaults to 'cms')
        :return: a dictionary with basic DID information
        """
        try:
            response = self.cli.get_did(scope=scope, name=didName, dynamic=dynamic)
        except DataIdentifierNotFound as exc:
            response = dict()
            self.logger.error("Data identifier not found in Rucio: %s. Error: %s", didName, str(exc))
        return response

    def getDataLockedAndAvailable(self, **kwargs):
        """
        This method retrieves all the locations where a given DID is
        currently available and locked. It can be used for the data
        location logic in global and local workqueue.

        Logic is as follows:
        1. look for all replication rules matching the provided keyword args
        2. location of single RSE rules and in state OK are set as a location
        3.a. if the input DID name is a block, get all the locks AVAILABLE for that block
          and compare the rule ID against the multi RSE rules. Keep the RSE if it matches
        3.b. otherwise - if it's a container - method `_getContainerLockedAndAvailable`
          gets called to resolve all the blocks and locks
        4. union of the single RSEs and the matched multi RSEs is returned as the
        final location of the provided DID

        :param kwargs: key/value pairs to filter out the rules. Most common filters are likely:
            scope: string with the scope name
            name: string with the DID name
            account: string with the rucio account name
            state: string with the state name
            grouping: string with the grouping name
        :return: a flat list with the RSE names locking and holding the input DID

        NOTE: some of the supported values can be looked up at:
        https://github.com/rucio/rucio/blob/master/lib/rucio/db/sqla/constants.py#L184
        """
        if 'name' not in kwargs:
            raise WMRucioException("A DID name must be provided to the getDataLockedAndAvailable API")
        if 'grouping' in kwargs:
            # long strings seem not to be working, like ALL / DATASET
            if kwargs['grouping'] == "ALL":
                kwargs['grouping'] = "A"
            elif kwargs['grouping'] == "DATASET":
                kwargs['grouping'] = "D"

        kwargs.setdefault("scope", "cms")

        multiRSERules = []
        finalRSEs = set()

        # First, find all the rules and where data is supposed to be locked
        rules = self.cli.list_replication_rules(kwargs)
        for rule in rules:
            # now resolve the RSE expressions
            rses = self.evaluateRSEExpression(rule['rse_expression'])
            if rule['copies'] == len(rses) and rule['state'] == "OK":
                # then we can guarantee that data is locked and available on these RSEs
                finalRSEs.update(set(rses))
            else:
                multiRSERules.append(rule['id'])
        self.logger.debug("Data location for %s from single RSE locks and available at: %s",
                          kwargs['name'], list(finalRSEs))
        if not multiRSERules:
            # then that is it, we can return the current RSEs holding and locking this data
            return list(finalRSEs)

        # At this point, we might already have some of the RSEs where the data is available and locked
        # Now check dataset locks and compare those rules against our list of multi RSE rules
        if self.isContainer(kwargs['name']):
            # It's a container! Find what those RSEs are and add them to the finalRSEs set
            rseLocks = self._getContainerLockedAndAvailable(multiRSERules, **kwargs)
            self.logger.debug("Data location for %s from multiple RSE locks and available at: %s",
                              kwargs['name'], list(rseLocks))
        else:
            # It's a single block! Find what those RSEs are and add them to the finalRSEs set
            rseLocks = set()
            for blockLock in self.cli.get_dataset_locks(kwargs['scope'], kwargs['name']):
                if blockLock['state'] == 'OK' and blockLock['rule_id'] in multiRSERules:
                    rseLocks.add(blockLock['rse'])
            self.logger.debug("Data location for %s from multiple RSE locks and available at: %s",
                              kwargs['name'], list(rseLocks))

        finalRSEs = list(finalRSEs | rseLocks)
        return finalRSEs

    def _getContainerLockedAndAvailable(self, multiRSERules, **kwargs):
        """
        This method is only supposed to be called internally (private method),
        because it won't consider the container level rules.

        This method retrieves all the locations where a given container DID is
        currently available and locked. It can be used for the data
        location logic in global and local workqueue.

        Logic is as follows:
        1. find all the blocks in the provided container name
        2. loop over all the block names and fetch their locks AVAILABLE
        2.a. compare the rule ID against the multi RSE rules. Keep the RSE if it matches
        3.a. if keyword argument grouping is ALL, returns an intersection of all blocks RSEs
        3.b. else - grouping DATASET - returns an union of all blocks RSEs

        :param multiRSERules: list of container level rules to be matched against
        :param kwargs: key/value pairs to filter out the rules. Most common filters are likely:
            scope: string with the scope name
            name: string with the DID name
            account: string with the rucio account name
            state: string with the state name
            grouping: string with the grouping name
        :return: a set with the RSE names locking and holding this input DID

        NOTE: some of the supported values can be looked up at:
        https://github.com/rucio/rucio/blob/master/lib/rucio/db/sqla/constants.py#L184
        """
        finalRSEs = set()
        blockNames = self.getBlocksInContainer(kwargs['name'])
        self.logger.debug("Container: %s contains %d blocks. Querying dataset_locks ...",
                          kwargs['name'], len(blockNames))

        rsesByBlocks = {}
        for block in blockNames:
            rsesByBlocks.setdefault(block, set())
            ### FIXME: feature request made to the Rucio team to support bulk operations:
            ### https://github.com/rucio/rucio/issues/3982
            for blockLock in self.cli.get_dataset_locks(block, kwargs['name']):
                if blockLock['state'] == 'OK' and blockLock['rule_id'] in multiRSERules:
                    rsesByBlocks[block].add(blockLock['rse'])

        ### The question now is:
        ###   1. do we want to have a full copy of the container in the same RSEs (grouping=A)
        ###   2. or we want all locations holding at least one block of the container (grouping=D)
        if kwargs['grouping'] == 'A':
            firstRun = True
            for _block, rses in rsesByBlocks.viewitems():
                if firstRun:
                    finalRSEs = rses
                    firstRun = False
                else:
                    finalRSEs = finalRSEs & rses
        else:
            for _block, rses in rsesByBlocks.viewitems():
                finalRSEs = finalRSEs | rses
        return finalRSEs
