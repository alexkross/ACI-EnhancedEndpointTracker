
from ... utils import get_db
from ... utils import get_redis

from .. utils import get_apic_session
from .. utils import get_attributes
from .. utils import get_class
from .. utils import get_controller_version
from .. utils import parse_apic_version
from .. utils import pretty_print
from .. subscription_ctrl import SubscriptionCtrl

from . common import MANAGER_CTRL_CHANNEL
from . common import MANAGER_WORK_QUEUE
from . common import MINIMUM_SUPPORTED_VERSION
from . common import MO_BASE
from . common import get_vpc_domain_id
from . ept_msg import MSG_TYPE
from . ept_msg import WORK_TYPE
from . ept_msg import eptMsg
from . ept_msg import eptMsgWork
from . ept_epg import eptEpg
from . ept_history import eptHistory
from . ept_node import eptNode
from . ept_tunnel import eptTunnel
from . ept_settings import eptSettings
from . ept_subnet import eptSubnet
from . ept_vnid import eptVnid
from . ept_vpc import eptVpc
from . mo_dependency_map import dependency_map

from importlib import import_module

import logging
import re
import threading
import time
import traceback

# module level logging
logger = logging.getLogger(__name__)

class eptSubscriber(object):
    def __init__(self, fabric):
        # receive instance of Fabric rest object
        self.fabric = fabric
        self.settings = eptSettings.load(fabric=self.fabric.fabric)
        self.initializing = True    # set to queue events until fully initialized
        self.stopped = False        # set to ignore events after hard_restart triggered
        self.db = None
        self.redis = None
        self.session = None
        self.soft_restart_ts = 0    # timestamp of last soft_restart
        self.manager_ctrl_channel_lock = threading.Lock()
        self.manager_work_queue_lock = threading.Lock()
        self.subscription_check_interval = 5.0   # interval to check subscription health

        # list of pending events received on subscription while in init state
        self.pending_events = []        
        self.pending_std_events = []
        # statically defined classnames in which to subscribe
        # slow subscriptions are classes which we expect a low number of events
        subscription_classes = [
            "fabricProtPol",        # handle_fabric_prot_pol
            "fabricAutoGEp",        # handle_fabric_group_ep
            "fabricExplicitGEp",    # handle_fabric_group_ep
            "fabricNode",           # handle_fabric_node
            #"vpcRsVpcConf",         # handle_rs_vpc_conf_event
            #"fvCtx",                # handle_vnid_event
            #"fvBD",                 # handle_vnid_event
            #"fvSvcBD",              # handle_vnid_event
            #"fvEPg",                # this includes fvAEPg, l3extInstP, vnsEPpInfo
            #"fvRsBd",
            #"vnsRsEPpInfoToBD",
            #"vnsRsLIfCtxToBD",      
            #"l3extExtEncapAllocator",
            #"fvSubnet",             # handle_subnet_event
            #"fvIpAttr",             # handle_subnet_event
        ]
        # classname to function handler for subscription events
        self.handlers = {                
            "fabricProtPol": self.handle_fabric_prot_pol,
            "fabricAutoGEp": self.handle_fabric_group_ep,
            "fabricExplicitGEp": self.handle_fabric_group_ep,
            "fabricNode": self.handle_fabric_node,
            #"fvSubnet": self.handle_subnet_event,
            #"fvIpAttr": self.handle_subnet_event,
            #"fvCtx": self.handle_vnid_event,
            #"fvBD": self.handle_vnid_event,
            #"fvSvcBD": self.handle_vnid_event,
        }

        # epm subscriptions expect a high volume of events
        epm_subscription_classes = [
            "epmMacEp",
            "epmIpEp",
            "epmRsMacEpToIpEpAtt",
        ]

        mo_classes = [
            "fvCtx",
            "fvBD",
            "fvSvcBD",
            "l3extExtEncapAllocator",
            "l3extInstP",
            "l3extOut",
            "l3extRsEctx",
            "fvAEPg",
            "fvRsBd",
            "vnsEPpInfo",
            "vnsRsEPpInfoToBD",
            "mgmtInB",
            "mgmtRsMgmtBD",
            "fvSubnet",
            "fvIpAttr",
            "vnsLIfCtx",
            "vnsRsLIfCtxToBD",
            "vpcRsVpcConf",
        ]
        # build mo classes into a dict where key is classname and value is imported object
        self.mo_classes = {}
        for mo in mo_classes:
            self.mo_classes[mo] = getattr(import_module(".%s" % mo, MO_BASE), mo)

        # create subscription object for slow and fast subscriptions
        slow_interest = {}
        epm_interest = {}

        for s in subscription_classes:
            slow_interest[s] = {"handler": self.handle_event}
        for s in self.mo_classes:
            slow_interest[s] = {"handler": self.handle_std_mo_event}
        for s in epm_subscription_classes:
            epm_interest[s] = {"handler": self.handle_event}

        self.slow_subscription = SubscriptionCtrl(
            self.fabric, 
            slow_interest, 
            heartbeat=300,
            inactive_interval=1
        )
        self.epm_subscription = SubscriptionCtrl(
            self.fabric, 
            epm_interest, 
            heartbeat=300,
            inactive_interval=0.01
        )

    def run(self):
        """ wrapper around run to handle interrupts/errors """
        logger.info("starting eptSubscriber for fabric '%s'", self.fabric.fabric)
        try:
            # allocate a unique db connection as this is running in a new process
            self.db = get_db(uniq=True, overwrite_global=True)
            self.redis = get_redis()
            self._run()
        except (Exception, SystemExit, KeyboardInterrupt) as e:
            logger.error("Traceback:\n%s", traceback.format_exc())
        finally:
            self.slow_subscription.unsubscribe()
            self.epm_subscription.unsubscribe()

    def _run(self):
        """ monitor fabric and enqueue work to workers """

        # setup slow subscriptions to catch events occurring during build
        #logger.debug("repeat heartbeat check...")
        #self.slow_subscription.heartbeat = 1
        #self.slow_subscription.subscribe(blocking=False)
        #self.build_mo()
        #time.sleep(3600)


        init_str = "initializing"
        # first step is to get a valid apic session, bail out if unable to connect
        self.fabric.add_fabric_event(init_str, "connecting to apic")
        self.session = get_apic_session(self.fabric)
        if self.session is None:
            logger.warn("failed to connect to fabric: %s", self.fabric.fabric)
            self.fabric.add_fabric_event("failed", "failed to connect to apic")
            return

        # get controller version, highlight mismatch and verify minimum version
        apic_version = get_controller_version(self.session)
        if len(apic_version) == 0:
            logger.warn("failed to determine apic version")
            self.fabric.add_fabric_event("failed", "failed to determine apic version")
            return
        apic_version_set = set([n["version"] for n in apic_version])
        if len(apic_version_set)>1:
            logger.warn("version mismatch for %s: %s", self.fabric.fabric, apic_version_set)
            self.fabric.add_fabric_event("warning", "version mismatch: %s" % ", ".join([
                    "apic-%s: %s" % (n["node"], n["version"]) for n in apic_version
                ]))
        # use whatever the first detected version is for validation, we don't expect version 
        # mismatch for controllers so warning is sufficient
        min_version = parse_apic_version(MINIMUM_SUPPORTED_VERSION)
        version = parse_apic_version(apic_version[0]["version"])
        self.fabric.add_fabric_event(init_str, "apic version: %s" % apic_version[0]["version"])
        if version is None or min_version is None:
            logger.warn("failed to parse apic version: %s (min version: %s)", version, min_version)
            self.fabric.add_fabric_event("failed","unknown or unsupported apic version: %s" % (
                apic_version[0]["version"]))
            self.fabric.auto_start = False
            self.fabric.save()
            return
        # will check major/min/build and ignore patch for version check for now
        min_matched = True
        if version["major"] < min_version["major"]:
            min_matched = False
        elif version["major"] == min_version["major"]:
            if version["minor"] < min_version["minor"]:
                min_matched = False
            elif version["minor"] == min_version["minor"]:
                min_matched = (version["build"] >= min_version["build"])
        if not min_matched:
            logger.warn("fabric does not meet minimum code version (%s < %s)", version, min_version)
            self.fabric.add_fabric_event("failed","unknown or unsupported apic version: %s" % (
                apic_version[0]["version"]))
            self.fabric.auto_start = False
            self.fabric.save()
            return

        # get overlay vnid and fabricProtP (which requires hard reset on change)
        vpc_attr = get_attributes(session=self.session, dn="uni/fabric/protpol")
        overlay_attr = get_attributes(session=self.session, dn="uni/tn-infra/ctx-overlay-1")
        if overlay_attr and "scope" in overlay_attr:
            self.settings.overlay_vnid = int(overlay_attr["scope"])
            if vpc_attr and "pairT" in vpc_attr:
                self.settings.vpc_pair_type = vpc_attr["pairT"]
                self.settings.save()
            else:
                logger.warn("failed to determine fabricProtPol pairT: %s (using default)",vpc_attr)
        else:
            logger.warn("failed to determine overlay vnid: %s", overlay_attr)
            self.fabric.add_fabric_event("failed", "unable to determine overlay-1 vnid")
            return
       
        # setup slow subscriptions to catch events occurring during build
        self.slow_subscription.subscribe(blocking=False)

        self.fabric.add_fabric_event(init_str, "collecting base managed objects")
        if not self.build_mo():
            self.fabric.add_fabric_event("failed", "failed to collect MOs")
            return

        # build node db and vpc db
        self.fabric.add_fabric_event(init_str, "building node db")
        if not self.build_node_db():
            self.fabric.add_fabric_event("failed", "failed to build node db")
            return
        if not self.build_vpc_db():
            self.fabric.add_fabric_event("failed", "failed to build node pc to vpc db")
            return

        # build tunnel db
        self.fabric.add_fabric_event(init_str, "building tunnel db")
        if not self.build_tunnel_db():
            self.fabric.add_fabric_event("failed", "failed to build tunnel db")
            return

        # build vnid db along with vnsLIfCtxToBD db which relies on vnid db
        self.fabric.add_fabric_event(init_str, "building vnid db")
        if not self.build_vnid_db():
            self.fabric.add_fabric_event("failed", "failed to build vnid db")
            return

        # build epg db
        self.fabric.add_fabric_event(init_str, "building epg db")
        if not self.build_epg_db():
            self.fabric.add_fabric_event("failed", "failed to build epg db")
            return

        # build subnet db
        self.fabric.add_fabric_event(init_str, "building subnet db")
        if not self.build_subnet_db():
            self.fabric.add_fabric_event("failed", "failed to build subnet db")
            return

        # setup epm subscriptions to catch events occurring during epm build
        #   self.epm_subscription.subscribe(blocking=False)
        # TODO - build endpoint database

        # subscriber running
        self.fabric.add_fabric_event("running")
        self.initializing = False

        # ensure that all subscriptions are active
        while True:
            if not self.slow_subscription.is_alive():
                logger.warn("slow subscription no longer alive for %s", self.fabric.fabric)
                self.fabric.add_fabric_event("failed", "subscription no longer alive")
                return
            if False and not self.epm_subscription.is_alive():
                logger.warn("epm subscription no longer alive for %s", self.fabric.fabric)
                self.fabric.add_fabric_event("failed", "subscription no longer alive")
                return
            time.sleep(self.subscription_check_interval)

    def hard_restart(self, reason=""):
        """ send msg to manager for fabric restart """
        logger.warn("restarting fabric monitor '%s': %s", self.fabric.fabric, reason)
        self.fabric.add_fabric_event("restarting", reason)
        # try to kill local subscriptions first
        try:
            self.stopped = True
            reason = "restarting: %s" % reason
            data = {"fabric":self.fabric.fabric, "reason":reason}
            msg = eptMsg(MSG_TYPE.FABRIC_RESTART,data=data)
            with self.manager_ctrl_channel_lock:
                self.redis.publish(MANAGER_CTRL_CHANNEL, msg.jsonify())
        finally:
            self.slow_subscription.unsubscribe()
            self.epm_subscription.unsubscribe()

    def soft_restart(self, ts=None, reason=""):
        """ soft restart sets initializing to True to block new updates along with restarting 
            slow_subscriptions.  A subset of tables are rebuilt which is much faster than a hard
            restart which requires updates to names (epg and vnid db), subnet db, and most 
            importantly endpoint db.
            The following tables are rebuilt in soft restart:
                - eptNode
                - eptTunnel
                - eptVpc
        """
        logger.debug("soft restart requested: %s", reason)
        if ts is not None and self.soft_restart_ts > ts:
            logger.debug("skipping stale soft_restart request (%.3f > %.3f)",self.soft_restart_ts,ts)
            return 

        init_str = "re-initializing"
        self.initializing = True
        self.slow_subscription.restart(blocking=False)

        # build node db and vpc db
        self.fabric.add_fabric_event("soft-reset", reason)
        self.fabric.add_fabric_event(init_str, "building node db")
        if not self.build_node_db():
            self.fabric.add_fabric_event("failed", "failed to build node db")
            return self.hard_restart("failed to build node db")
        # need to rebuild vpc db which requires a rebuild of local mo vpcRsVpcConf mo first
        success = self.mo_classes["vpcRsVpcConf"].rebuild(self.fabric, session=self.session)
        if not success or not self.build_vpc_db():
            self.fabric.add_fabric_event("failed", "failed to build node pc to vpc db")
            return self.hard_restart("failed to build node pc to vpc db")

        # build tunnel db
        self.fabric.add_fabric_event(init_str, "building tunnel db")
        if not self.build_tunnel_db():
            self.fabric.add_fabric_event("failed", "failed to build tunnel db")
            return self.hard_restart("failed to build tunnel db")

        # clear appropriate caches
        self.send_flush(eptNode)
        self.send_flush(eptVpc)
        self.send_flush(eptTunnel)

        self.fabric.add_fabric_event("running")
        self.initializing = False

    def send_msg(self, msg):
        """ send eptMsgWork to worker via manager work queue """
        # validate that 'fabric' is ALWAYS set on any work
        msg.fabric = self.fabric.fabric
        with self.manager_work_queue_lock:
            self.redis.rpush(MANAGER_WORK_QUEUE, msg.jsonify())

    def send_flush(self, collection):
        """ send flush message to workers for provided collection """
        logger.info("flush %s", collection._classname)
        # node addr of 0 is broadcast to all nodes of provided role
        msg = eptMsgWork(0, "worker", {"cache": collection._classname}, WORK_TYPE.FLUSH_CACHE)
        msg.qnum = 0    # highest priority queue
        self.send_msg(msg)

    def parse_event(self, event, verify_ts=True):
        """ iterarte list of (classname, attr) objects from subscription event including _ts 
            attribute representing timestamp when event was received if verify_ts is set
        """
        try:
            for e in event["imdata"]:
                classname = e.keys()[0]
                if "attributes" in e[classname]:
                        attr = e[classname]["attributes"]
                        if verify_ts:
                            if "_ts" in event: 
                                attr["_ts"] = event["_ts"]
                            else:
                                attr["_ts"] = time.time()
                        yield (classname, attr)
                else:
                    logger.warn("invalid event: %s", e)
        except Exception as e:
            logger.error("Traceback:\n%s", traceback.format_exc())

    def handle_event(self, event):
        """ generic handler to call appropriate handler based on event classname
            this will also enque events into buffer until intialization has completed
        """
        if self.stopped:
            logger.debug("ignoring event (subscriber stopped and waiting for reset)")
            return
        if self.initializing:
            self.pending_events.append(event)
            return
        logger.debug("event: %s", event)
        try:
            for (classname, attr) in self.parse_events(event):
                if classname not in self.handlers:
                    logger.warn("no event handler defined for classname: %s", classname)
                else:
                    return self.handlers[classname](classname, attr)
        except Exception as e:
            logger.error("Traceback:\n%s", traceback.format_exc())

    def handle_std_mo_event(self, event):
        """ handle standard MO subscription event. This will update the local db MO and if classname
            exists within dependency_map, will update each dependency. If MO exists within ept_map,
            will update each ept object.  Finally, will send a flush for each ept classname that 
            was changed.
        """
        if self.stopped:
            logger.debug("ignoring event (subscriber stopped and waiting for reset)")
            return
        if self.initializing:
            self.pending_std_events.append(event)
            return
        try:
            for (classname, attr) in self.parse_event(event):
                if classname not in self.mo_classes or "dn" not in attr or "status" not in attr:
                    logger.warn("event received for unknown classname: %s, %s", classname, event)
                    continue

                if classname in dependency_map:
                    logger.debug("triggering sync_event for dependency %s", classname)
                    updates = dependency_map[classname].sync_event(self.fabric.fabric, attr, 
                        session = self.session
                    )
                    logger.debug("updated objects: %s", len(updates))
                else:
                    logger.warn("%s not defined in dependency_map", classname)

        except Exception as e:
            logger.error("Traceback:\n%s", traceback.format_exc())

    def build_mo(self):
        """ build managed objects for defined classes """
        for mo in sorted(self.mo_classes):
            self.mo_classes[mo].rebuild(self.fabric, session=self.session)
        return True

    def initialize_ept_collection(self, eptObject, mo_classname, attribute_map=None, 
            regex_map=None ,set_ts=False, flush=False):
        """ initialize ept collection.  Note, mo_object or mo_classname must be provided
                eptObject = eptNode, eptVnid, eptEpg, etc...
                mo_classname = classname of mo used for query, or if exists within self.mo_classes,
                                the mo object from local database
                set_ts = boolean to set modify ts within ept object. If mo_object is set, then ts
                            from mo object is written to ept object. Else, timestamp of APIC query
                            is used
                flush = boolean to flush ept collection at initialization
                attribute_map = dict handling mapping of ept attribute to mo attribute. If omitted,
                        then the attribute map will use the value from the corresponding 
                        DependencyNode (if found within the dependency_map)
                regex_map = dict - ept attribute names in regex map will contain a regex used to 
                        extract the value from the corresponding mo attribute.  if omitted, then 
                        will use the regex_map definied within the corresponding DependencyNode
                        (if found within the dependency_map)

                        This regex must contain a named capture group of 'value'.  For example:
                        attribute_map = {
                            "node": "dn"        # set's the ept value of 'node' to the mo 'dn'
                        }
                        regex_map = {
                            "node": "node-(?P<value>[0-9]+)/" # extract interger value from 'node'
                        }

            return bool success

        """
        # iterator over data from class query returning just dict attributes
        def raw_iterator(data):
            for attr in get_attributes(data=data):
                yield attr

        # iterator over mo objects returning just dict attributes
        def mo_iterator(objects):
            for o in objects:
                yield o.to_json()

        # get data from local mo db
        if mo_classname in self.mo_classes:
            data = self.mo_classes[mo_classname].find(fabric=self.fabric.fabric)
            iterator = mo_iterator
        else:
            data = get_class(self.session, mo_classname)
            if data is None:
                logger.warn("failed to get data for classname %s", mo_classname)
                return False
            iterator = raw_iterator

        # get attribute_map and regex_map from arguments or dependency map
        default_attribute_map = {}
        default_regex_map = {}
        if mo_classname in dependency_map:
            default_attribute_map = dependency_map[mo_classname].ept_attributes
            default_regex_map = dependency_map[mo_classname].ept_regex_map
        if attribute_map is None: 
            attribute_map = default_attribute_map
        if regex_map is None:
            regex_map = default_regex_map

        ts = time.time()
        bulk_objects = []
        # iterate over results 
        for attr in iterator(data):
            db_obj = {}
            for db_attr, o_attr in attribute_map.items():
                # can only map 'plain' string attributes (not list referencing parent objects)
                if isinstance(o_attr, basestring) and o_attr in attr:
                    # check for regex_map
                    if db_attr in regex_map:
                        r1 = re.search(regex_map[db_attr], attr[o_attr])
                        if r1:
                            if "value" in r1.groupdict():
                                db_obj[db_attr] = r1.group("value")
                            else: 
                                db_obj[attr] = attr[o_attr]
                        else:
                            logger.warn("%s value %s does not match regex %s", o_attr,attr[o_attr], 
                                regex_map[db_attr])
                            db_obj = {}
                            break
                    else:
                        db_obj[db_attr] = attr[o_attr]
            if len(db_obj)>0:
                db_obj["fabric"] = self.fabric.fabric
                if set_ts: 
                    if "ts" in attr: db_obj["ts"] = attr["ts"]
                    else: db_obj["ts"] = ts
                bulk_objects.append(eptObject(**db_obj))
            else:
                logger.warn("%s object not added from MO (no matching attributes): %s", 
                    eptObject._classname, attr)

        # flush right before insert to minimize time of empty table
        if flush:
            logger.debug("flushing %s entries for fabric %s",eptObject._classname,self.fabric.fabric)
            eptObject.delete(_filters={"fabric":self.fabric.fabric})
        if len(bulk_objects)>0:
            eptObject.bulk_save(bulk_objects, skip_validation=False)
        else:
            logger.debug("no objects of %s to insert", mo_classname)
        return True
    
    def build_node_db(self):
        """ initialize node collection and vpc nodes. return bool success """
        logger.debug("initializing node db")
        if not self.initialize_ept_collection(eptNode, "topSystem", attribute_map = {
                "addr": "address",
                "name": "name",
                "node": "id",
                "oob_addr": "oobMgmtAddr",
                "pod_id": "podId",
                "role": "role",
                "state": "state",
            }, flush=True):
            logger.warn("failed to build node db from topSystem")
            return False

        # maintain list of all nodes for id to addr lookup 
        all_nodes = {}
        for n in eptNode.find(fabric=self.fabric.fabric):
            all_nodes[n.node] = n

        # create pseudo node for each vpc group from fabricAutoGEp and fabricExplicitGEp each of 
        # which contains fabricNodePEp
        vpc_type = "fabricExplicitGEp"
        node_ep = "fabricNodePEp"
        data = get_class(self.session, vpc_type, rspSubtree="full", rspSubtreeClass=node_ep)
        if data is None or len(data) == 0:
            logger.debug("no vpcs found for fabricExplicitGEp, checking fabricAutoGEp")
            vpc_type = "fabricAutoGEp"
            data = get_class(self.session, vpc_type, rspSubtree="full", rspSubtreeClass=node_ep)
            if data is None or len(data) == 0:
                logger.debug("no vpc configuration found")
                return True

        # build all known vpc groups
        bulk_objects = []
        for obj in data:
            if vpc_type in obj and "attributes" in obj[vpc_type]:
                attr = obj[vpc_type]["attributes"]
                if "virtualIp" in attr and "name" in attr and "dn" in attr:
                    name = attr["name"]
                    addr = re.sub("/[0-9]+$", "", attr["virtualIp"])
                    # get children node_ep (expect exactly 2)
                    child_nodes = []
                    if "children" in obj[vpc_type]:
                        for cobj in obj[vpc_type]["children"]:
                            if node_ep in cobj and "attributes" in cobj[node_ep]:
                                cattr = cobj[node_ep]["attributes"]
                                if "id" in cattr and "peerIp" in cattr:
                                    peer_ip = re.sub("/[0-9]+$", "", cattr["peerIp"])
                                    node_id = int(cattr["id"])
                                    if node_id in all_nodes:
                                        child_nodes.append({
                                            "local_node": all_nodes[node_id]
                                        })
                                    else:
                                        logger.warn("unknown node id %s in %s", node_id, vpc_type)
                                else:
                                    logger.warn("invalid %s object: %s", node_ep, cobj)
                    if len(child_nodes) == 2:
                        vpc_domain_id = get_vpc_domain_id(
                            child_nodes[0]["local_node"].node,
                            child_nodes[1]["local_node"].node,
                        )
                        bulk_objects.append(eptNode(fabric=self.fabric.fabric,
                            addr=addr,
                            name=name,
                            node=vpc_domain_id,
                            pod_id=child_nodes[0]["local_node"].pod_id,
                            role="vpc",
                            state="in-service",
                            nodes=[
                                {
                                    "node": child_nodes[0]["local_node"].node,
                                    "addr": child_nodes[0]["local_node"].addr,
                                },
                                {
                                    "node": child_nodes[1]["local_node"].node,
                                    "addr": child_nodes[1]["local_node"].addr,
                                },
                            ],
                        ))
                    else:
                        logger.warn("expected 2 %s child objects: %s", node_ep,obj)
                else:
                    logger.warn("invalid %s object: %s", vpc_type, obj)
        
        if len(bulk_objects)>0:
            eptNode.bulk_save(bulk_objects, skip_validation=False)
        return True

    def build_tunnel_db(self):
        """ initialize tunnel db. return bool success """
        logger.debug("initializing tunnel db")
        return self.initialize_ept_collection(eptTunnel, "tunnelIf", attribute_map={
                "node": "dn",
                "intf": "id",
                "dst": "dest",
                "src": "src",
                "status": "operSt",
                "encap": "tType",
                "flags": "type",
            }, regex_map = {
                "node": "topology/pod-[0-9]+/node-(?P<value>[0-9]+)/",
                "src": "(?P<value>[^/]+)(/[0-9]+)?",
            }, flush=True, set_ts=True)

    def build_vpc_db(self):
        """ build port-channel to vpc interface mapping. return bool success """
        logger.debug("initializing vpc db")
        # vpcRsVpcConf exists within self.mo_classses and already defined in dependency_map
        return self.initialize_ept_collection(eptVpc,"vpcRsVpcConf",set_ts=True, flush=True)

    def build_vnid_db(self):
        """ initialize vnid database. return bool success
            vnid objects include the following:
                fvCtx (vrf)
                fvBD (BD)
                fvSvcBD (copy-service BD)
                l3extExtEncapAllocator (external BD)
        """
        logger.debug("initializing vnid db")
       
        # handle fvCtx, fvBD, and fvSvcBD
        logger.debug("bulding vnid from fvCtx")
        if not self.initialize_ept_collection(eptVnid, "fvCtx", set_ts=True, flush=True):
            logger.warn("failed to initialize vnid db for fvCtx")
            return False
        logger.debug("bulding vnid from fvBD")
        if not self.initialize_ept_collection(eptVnid, "fvBD", set_ts=True, flush=False):
            logger.warn("failed to initialize vnid db for fvBD")
            return False
        logger.debug("bulding vnid from fvSvcBD")
        if not self.initialize_ept_collection(eptVnid, "fvSvcBD", set_ts=True, flush=False):
            logger.warn("failed to initialize vnid db for fvSvcBD")
            return False

        # dict of name (vrf/bd) to vnid for quick lookup
        logger.debug("bulding vnid from l3extExtEncapAllocator")
        ts = time.time()
        bulk_objects = []
        vnids = {}  
        l3ctx = {}     # mapping of l3out name to vrf vnid
        for v in eptVnid.find(fabric=self.fabric.fabric): 
            vnids[v.name] = v.vnid
        for c in self.mo_classes["l3extRsEctx"].find(fabric=self.fabric.fabric):
            if c.tDn in vnids:
                l3ctx[c.parent]  = vnids[c.tDn]
            else:
                logger.warn("failed to map l3extRsEctx tDn(%s) to vrf vnid", c.tDn)
        for obj in self.mo_classes["l3extExtEncapAllocator"].find(fabric=self.fabric.fabric):
            new_vnid = eptVnid(
                fabric = self.fabric.fabric,
                vnid = int(re.sub("vxlan-","", obj.extEncap)),
                name = obj.dn,
                encap = obj.encap,
                ts = ts
            )
            if obj.parent in l3ctx:
                new_vnid.vrf = l3ctx[obj.parent]
            else:
                logger.warn("failed to map l3extOut(%s) to vrf vnid", obj.parent)
            bulk_objects.append(new_vnid)

        if len(bulk_objects)>0:
            eptVnid.bulk_save(bulk_objects, skip_validation=False)
        return True

    def build_epg_db(self):
        """ initialize epg database. return bool success
            epg objects include the following (all instances of fvEPg)
                fvAEPg      - normal epg            (fvRsBd - map to fvBD)
                mgmtInB     - inband mgmt epg       (mgmtRsMgmtBD - map to fvBD)
                vnsEPpInfo  - epg from l4 graph     (vnsRsEPpInfoToBD - map to fvBD)
                l3extInstP  - external epg          (no BD)
        """
        logger.debug("initializing epg db")
        flush = True
        for c in ["fvAEPg", "mgmtInB", "vnsEPpInfo", "l3extInstP"]:
            if not self.initialize_ept_collection(eptEpg, c, set_ts=True, flush=flush):
                logger.warn("failed to initialize epg db from %s", c)
                return False
            # only flush on first table
            flush = False

        logger.debug("mapping epg to bd vnid")
        # need to build mapping of epg to bd. to do so need to get the dn of the BD for each epg
        # and then lookup into vnids table for bd name to get bd vnid to merge into epg table
        bulk_object_keys = {}   # dict to prevent duplicate addition of object to bulk_objects
        bulk_objects = []
        vnids = {}      # indexed by bd/vrf name (dn), contains only vnid
        epgs = {}       # indexed by epg name (dn), contains full object
        for v in eptVnid.find(fabric=self.fabric.fabric): 
            vnids[v.name] = v.vnid
        for e in eptEpg.find(fabric=self.fabric.fabric):
            epgs[e.name] = e
        for classname in ["fvRsBd", "vnsRsEPpInfoToBD", "mgmtRsMgmtBD"]:
            logger.debug("map epg bd vnid from %s", classname)
            for mo in self.mo_classes[classname].find(fabric=self.fabric.fabric):
                epg_name = re.sub("/(rsbd|rsEPpInfoToBD|rsmgmtBD)$", "", mo.dn)
                bd_name = mo.tDn 
                if epg_name not in epgs:
                    logger.warn("cannot map bd to unknown epg '%s' from '%s'", epg_name, classname)
                    continue
                if bd_name not in vnids:
                    logger.warn("cannot map epg %s to unknown bd '%s'", epg_name, bd_name)
                    continue
                epgs[epg_name].bd = vnids[bd_name]
                if epg_name not in bulk_object_keys:
                    bulk_object_keys[epg_name] = 1
                    bulk_objects.append(epgs[epg_name])
                else:
                    logger.warn("skipping duplicate dn: %s", epg_name)

        if len(bulk_objects)>0:
            # only adding vnid here which was validated from eptVnid so no validation required
            eptEpg.bulk_save(bulk_objects, skip_validation=True)
        return True

    def build_subnet_db(self):
        """ build subnet db 
            Only two objects that we care about but they can come from a few different places:
                - fvSubnet
                    - fvBD, fvAEPg
                      vnsEPpInfo and vnsLIfCtx where the latter requires vnsRsLIfCtxToBD lookup
                - fvIpAttr
                    - fvAEPg
        """
        logger.debug("initializing subnet db")

        # use subnet dn as lookup into vnid and epg table to determine corresponding bd vnid
        # yes, we're doing duplicate db lookup as build_epg_db but db lookup on init is minimum
        # performance hit even with max scale
        vnids = {}
        epgs = {}
        for v in eptVnid.find(fabric=self.fabric.fabric): 
            vnids[v.name] = v.vnid
        for e in eptEpg.find(fabric=self.fabric.fabric):
            # we only care about the bd vnid, only add to epgs list if a non-zero value is present
            if e.bd != 0: epgs[e.name] = e.bd
        # although not technically an epg, eptVnsLIfCtxToBD contains a mapping to bd that we need
        for mo in self.mo_classes["vnsRsLIfCtxToBD"].find(fabric=self.fabric.fabric):
            if mo.tDn in vnids:
                epgs[mo.parent] = vnids[mo.tDn]
            else:
                logger.warn("%s tDn %s not in vnids", mo._classname, mo.tDn)

        bulk_objects = []
        # should now have all objects that would contain a subnet 
        for classname in ["fvSubnet", "fvIpAttr"]:
            data = get_class(self.session, classname)
            ts = time.time()
            if data is None:
                logger.warn("failed to get data for classname: %s", classname)
                continue
            for attr in get_attributes(data=data):
                if "ip" not in attr or "dn" not in attr:
                    logger.warn("invalid %s object (missing dn/ip): %s", classname, attr)
                    continue
                dn = re.sub("(/crtrn/ipattr-.+$|/subnet-\[[^]]+\]$)","", attr["dn"])
                # usually in bd so check vnid first and then epg
                bd_vnid = None
                if dn in vnids:
                    bd_vnid = vnids[dn]
                elif dn in epgs:
                    bd_vnid = epgs[dn]
                if bd_vnid is not None:
                    # FYI - we support fvSubnet on BD and EPG for shared services so duplicate ip
                    # can exist. unique index is disabled on eptSubnet to support this... 
                    bulk_objects.append(eptSubnet(
                        fabric = self.fabric.fabric,
                        bd = bd_vnid,
                        name = attr["dn"],
                        ip = attr["ip"],
                        ts = ts
                    ))
                else:
                    logger.warn("failed to map subnet '%s' (%s) to a bd", attr["dn"], dn)

        logger.debug("flushing entries in %s for fabric %s",eptSubnet._classname,self.fabric.fabric)
        eptSubnet.delete(_filters={"fabric":self.fabric.fabric})
        if len(bulk_objects)>0:
            eptSubnet.bulk_save(bulk_objects, skip_validation=False)
        return True

    def handle_fabric_prot_pol(self, classname, attr):
        """ if pairT changes in fabricProtPol then trigger hard restart """
        logger.debug("handle fabricProtPol event: %s", attr["pairT"])
        if "pairT" in attr and attr["pairT"] != self.settings.vpc_pair_type:
            msg="fabricProtPol changed from %s to %s" % (self.settings.vpc_pair_type,attr["pairT"])
            logger.warn(msg)
            self.hard_restart(msg)
        else:
            logger.debug("no change in fabricProtPol")

    def handle_fabric_group_ep(self, classname, attr):
        """ fabricExplicitGEp or fabricAutoGEp update requires unconditional soft restart """
        logger.debug("handle %s event", classname)
        self.soft_restart(ts=attr["_ts"], reason="(%s) vpc domain update" % classname)

    def handle_fabric_node(self, classname, attr):
        """ handle events for fabricNode
            If a new leaf becomes active then trigger a hard restart to rebuild endpoint database
            as there's no way of knowing when endpoint events were missed on the new node (also,
            we need to restart both slow and epm subscriptions to get events from the new node).
            If an existing leaf becomes inactive, then create delete jobs for all endpoint learns 
            for this leaf
        """
        logger.debug("handle fabricNode event: %s", attr["dn"])
        if "dn" in attr and "fabricSt" in attr:
            r1 = re.search("topology/pod-(?P<pod>[0-9]+)/node-(?P<node>[0-9]+)", attr["dn"])
            status = attr["fabricSt"]
            if r1 is None:
                logger.warn("failed to extract node id from fabricNode dn: %s", attr["dn"])
                return
            # get db entry for this node
            node = eptNode.load(fabric=self.fabric.fabric, node=int(r1.group("node")))
            if node.exists():
                if node.role != "leaf":
                    logger.debug("ignoring fabricNode event for '%s'", node.role)
                else:
                    # if this is an active event, then trigger a hard restart else trigger pseudo
                    # delete jobs for all previous entries on node.  This includes XRs to account
                    # for bounce along with generally cleanup of node state.
                    if status == "active":
                        self.hard_restart(reason="leaf '%s' became active" % node.node)
                    else:
                        logger.debug("node %s '%s', sending watch_node event", node.node, status)
                        data = {"pod": node.pod_id, "node": node.node}
                        msg = eptMsgWork("node-%s" % node.node, "watcher",data,WORK_TYPE.WATCH_NODE)
                        self.send_msg(msg)
            else:
                if status != "active":
                    logger.debug("ignorning '%s' event for unknown node: %s",status,r1.group("node"))
                else:
                    # a new node became active, double check that is a leaf and if so trigger a 
                    # hard restart
                    new_node_dn = "topology/pod-%s/node-%s" % (r1.group("pod"), r1.group("node"))
                    new_attr = get_attributes(session=self.session, dn=new_node_dn)
                    if new_attr is not None and "role" in new_attr and new_attr["role"] == "leaf":
                        self.hard_restart(reason="new leaf '%s' became active" % r1.group("node"))
                    else:
                        logger.debug("ignorning active event for non-leaf")
        else:
            logger.debug("ignoring fabricNode event (fabricSt or dn not present in attributes)")
