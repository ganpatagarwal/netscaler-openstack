
# Copyright 2015 Citrix Systems, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import abc
import re
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils


from neutron import context as ncontext
from neutron.i18n import _LE
from oslo_service import service
from neutron.plugins.common import constants

from neutron_lbaas.drivers import driver_base
from neutron_lbaas.drivers.driver_mixins import BaseManagerMixin
from neutron_lbaas.services.loadbalancer.drivers.netscaler import ncc_client

DEFAULT_PERIODIC_TASK_INTERVAL = "2"
DEFAULT_STATUS_COLLECTION = "True"
DEFAULT_PAGE_SIZE = "300"
DEFAULT_IS_SYNCRONOUS = "True"

PROV = "provisioning_status"
NETSCALER = "netscaler"

LOG = logging.getLogger(__name__)

NETSCALER_CC_OPTS = [
    cfg.StrOpt(
        'netscaler_ncc_uri',
        help=_('The URL to reach the NetScaler Control Center Server.'),
    ),
    cfg.StrOpt(
        'netscaler_ncc_username',
        help=_('Username to login to the NetScaler Control Center Server.'),
    ),
    cfg.StrOpt(
        'netscaler_ncc_password',
        help=_('Password to login to the NetScaler Control Center Server.'),
    ),
    cfg.StrOpt(
        'periodic_task_interval',
        default=DEFAULT_PERIODIC_TASK_INTERVAL,
        help=_('Setting for periodic task collection interval from'
               'NetScaler Control Center Server..'),
    ),
    cfg.StrOpt(
        'is_synchronous',
        default=DEFAULT_IS_SYNCRONOUS,
        help=_('Setting for option to enable synchronous operations'
               'NetScaler Control Center Server.'),
    ),
    cfg.StrOpt(
        'netscaler_ncc_cleanup_mode',
        help=_(
            'Setting to enable/disable cleanup mode for NetScaler Control '
            'Center Server'),
    ),
    cfg.StrOpt(
        'netscaler_status_collection',
        default=DEFAULT_STATUS_COLLECTION + "," + DEFAULT_PAGE_SIZE,
        help=_('Setting for member status collection from'
               'NetScaler Control Center Server.'),
    )
]


if not hasattr(cfg.CONF, "netscaler_driver"):
    cfg.CONF.register_opts(NETSCALER_CC_OPTS, 'netscaler_driver')


LBS_RESOURCE = 'loadbalancers'
LB_RESOURCE = 'loadbalancer'
LISTENERS_RESOURCE = 'listeners'
LISTENER_RESOURCE = 'listener'
POOLS_RESOURCE = 'pools'
POOL_RESOURCE = 'pool'
MEMBERS_RESOURCE = 'members'
MEMBER_RESOURCE = 'member'
MONITORS_RESOURCE = 'healthmonitors'
MONITOR_RESOURCE = 'healthmonitor'
STATS_RESOURCE = 'stats'
PROV_SEGMT_ID = 'provider:segmentation_id'
PROV_NET_TYPE = 'provider:network_type'
DRIVER_NAME = 'netscaler_driver'
RESOURCE_PREFIX = 'v2.0/lbaas'
STATUS_PREFIX = 'oca/v2'
ADMIN_PREFIX = 'admin/v1'
JOURNAL_CONTEXTS = 'journalcontexts'
MEMBER_STATUS = 'memberstatus'
PAGE = 'page'
SIZE = 'size'
GET_METHOD={}
GET_METHOD["PENDING_CREATE"] = "POST"
GET_METHOD["PENDING_UPDATE"] = "PUT"
GET_METHOD["PENDING_DELETE"] = "DELETE"
ITEM_NOT_FOUND="ItemNotFound"

PROVISIONING_STATUS_TRACKER = []


class NetScalerLoadBalancerDriverV2(driver_base.LoadBalancerBaseDriver):

    def __init__(self, plugin):
        super(NetScalerLoadBalancerDriverV2, self).__init__(plugin)

        self.driver_conf = cfg.CONF.netscaler_driver
        self.admin_ctx = ncontext.get_admin_context()
        self._init_client()
        self._init_managers()
        self._init_status_collection()

    def _init_client(self):
        self.ncc_uri = self.driver_conf.netscaler_ncc_uri
        self.ncc_username = self.driver_conf.netscaler_ncc_username
        self.ncc_password = self.driver_conf.netscaler_ncc_password
        self.ncc_cleanup_mode = cfg.CONF.netscaler_driver.netscaler_ncc_cleanup_mode
        self.client = ncc_client.NSClient(self.ncc_uri,
                                          self.ncc_username,
                                          self.ncc_password,
                                          self.ncc_cleanup_mode)

    def _init_managers(self):
        self.load_balancer = NetScalerLoadBalancerManager(self)
        self.listener = NetScalerListenerManager(self)
        self.pool = NetScalerPoolManager(self)
        self.member = NetScalerMemberManager(self)
        self.health_monitor = NetScalerHealthMonitorManager(self)

    def _init_status_collection(self):
        self.status_conf = self.driver_conf.netscaler_status_collection
        self.periodic_task_interval = self.driver_conf.periodic_task_interval
        status_conf = self.driver_conf.netscaler_status_collection
        (is_status_collection,
            pagesize_status_collection) = status_conf.split(",")
        self.is_status_collection = True
        if is_status_collection.lower() == "false":
            self.is_status_collection = False
        self.pagesize_status_collection = pagesize_status_collection
        NetScalerStatusService(self).start()

    def collect_provision_status(self):
        LOG.debug("collecting provision status")
        admin_ctx = ncontext.get_admin_context()
        db_lbs = self.plugin.db.get_loadbalancers(
                                                  admin_ctx)
        LOG.debug("all loadbalancers from db are %s" % repr(db_lbs))
        for db_lb in db_lbs:
            if db_lb.provider is not None and db_lb.provisioning_status is not None :
                if ((db_lb.provider.provider_name == NETSCALER) and
                        (db_lb.provisioning_status.startswith("PENDING_"))):
                    self._update_status_tree_in_db(db_lb)
            else :
                try:
                    LOG.info("loadbalancer provider is %s and provisioning_status is %s" % (repr(db_lb.provider),repr(db_lb.provisioning_status)))
                    LOG.info("loadbalancer with id %s does not have provider or provisioning status" % repr(db_lb.id))
                    LOG.info("loadbalancer with name %s does not have provider or provisioning status" % repr(db_lb.name))
                except Exception :
                    LOG.exception(
                          _LE("loadbalancer stored in neutron database is not complete"))
                     
                
    def _update_status_tree_in_db(self, db_lb):
            LOG.debug("status tree to be updated is %s" % repr(db_lb.id))
            for db_listener in db_lb.listeners:
                db_listener.loadbalancer = db_lb
                track= self._track_entity(db_listener, "listeners", self.listener)
                LOG.debug("tracked listener %s" % repr(self.listener))
                if not track:
                    return
                db_pool = db_listener.default_pool
                if db_pool:
                    db_pool.listener = db_listener
                    track = self._track_entity(db_pool, "pools", self.pool)
                    LOG.debug("tracked pool %s" % repr(self.pool))
                    if not track:
                        return
                    db_members = db_pool.members
                    LOG.debug("members are %s" % repr(db_members))
                    for db_member in db_members:
                        db_member.pool = db_pool
                        LOG.debug("member id is %s" % repr(db_member.id))
                        track = self._track_entity(db_member, "members", self.member)
                        LOG.debug("tracked member %s" % repr(self.member))
                        if not track:
                            return
                    db_hm = db_pool.healthmonitor
                    LOG.debug("healthmonitors is %s" % repr(db_hm))
                    if db_hm:
                        db_hm.pool = db_pool
                        LOG.debug("healthmonitors inside if is %s" % repr(db_hm))
                        track = self._track_entity(db_hm, "healthmonitors", self.health_monitor)
                        LOG.debug("tracked healthmonitor %s" % repr(self.health_monitor))
                        if not track:
                            return
            self._track_entity(db_lb, "loadbalancers", self.load_balancer)
            LOG.debug("tracked loadbalancer %s" % repr(self.load_balancer))
                        
    def _track_entity(self, db_entity, entity_type, entity_manager):
        if db_entity.provisioning_status == constants.ACTIVE or db_entity.provisioning_status == constants.ERROR :
            return True
        delete_entity = False
        if self.ncc_cleanup_mode.lower() == "true" :
            if db_entity.provisioning_status == constants.PENDING_DELETE :
                delete_entity = True
                self.do_successful_completion_after_tracking(db_entity, entity_manager, delete_entity) 
                return True 
            else :
                return True
        status, message, error_reason = self._get_task_status(entity_type, db_entity)
        if status:
            if status == "Finished" :
                LOG.debug("status  of entity %s is Finished" % repr(db_entity))
                
                ''' if entity is in PENDING_DELETE and status is Finished,implies successfull deletion from controlcenter,
                we need to delete it from openstack neutron db as well..
                For all other cases,we mark it ACTIVE by calling successful completion'''
                
                if db_entity.provisioning_status == constants.PENDING_DELETE:
                    delete_entity = True
                self.do_successful_completion_after_tracking(db_entity, entity_manager, delete_entity)
                return True
            elif re.match("Error*",status):
                LOG.debug("status  of entity %s is Error.\n Message returned by controlcenter is %s" % (repr(db_entity),message))
                if error_reason == ITEM_NOT_FOUND and db_entity.provisioning_status == constants.PENDING_DELETE:
                    delete_entity = True
                    self.do_successful_completion_after_tracking(db_entity, entity_manager, delete_entity)
                else :
                    try:
                        entity_manager.failed_completion(
                                                     self.admin_ctx, db_entity)
                    except Exception:
                        LOG.error(_LE("error with failed completion"))
                return True
            else:
                return False  
        else :
            LOG.info("status  of entity %s is not received." % (repr(db_entity)))
            LOG.info("Entity might be deleted from the control center \
                            and its status in neutron db is %s." % repr(db_entity.provisioning_status))       
            return True
        
    def do_successful_completion_after_tracking(self, db_entity,entity_manager,delete_entity):
        try:
            entity_manager.successful_completion(
                    self.admin_ctx, db_entity, delete=delete_entity)
        except Exception:
            LOG.error(_LE("error with successful completion"))
 
    def _get_task_status(self,entity_type, entity): 
        ''' example resource path sent to NCC will be ncc_ip/admin/v1/journalcontexts?filter=operation:POST,entity_type:vips,entity_id:54a3-4bee-ae08-70f840c83ae0 '''      
        resource_path = "%s/%s?filter=operation:%s,entity_type:%s,entity_id:%s" % (ADMIN_PREFIX,JOURNAL_CONTEXTS,GET_METHOD[entity.provisioning_status],entity_type,entity.id)
        LOG.debug("resource path is %s" % repr(resource_path))
        status = None
        message = None
        result = None
        error_reason = None
        try:
            __,result = (self.client.retrieve_resource("GLOBAL",resource_path))
             
        except Exception:
            LOG.error("Request to get journal context from NMAS failed")
            return status,message,error_reason
        
        if result == None :
            LOG.debug("result of GET journal context api is None")
            return status,message,error_reason
        
        if "body" in result :
            result = jsonutils.loads(result['body'])
            
        LOG.debug("result of GET journalcontexts is %s" % repr(result))
        if "journalcontexts" in result and len(result["journalcontexts"]) > 0 :
            status = result['journalcontexts'][0]['status']
            message = result['journalcontexts'][0]['message']
            error_reason =  result['journalcontexts'][0]['error_reason']
        LOG.debug("status and error reason returned from controlcenter is %s and %s" % (status,error_reason))
        return status,message,error_reason
                    
class NetScalerCommonManager(BaseManagerMixin):

    def __init__(self, driver):
        super(NetScalerCommonManager, self).__init__(driver)
        self.payload_preparer = PayloadPreparer()
        self.client = ncc_client.NSClient(driver.ncc_uri,
                                          driver.ncc_username,
                                          driver.ncc_password,
                                          driver.ncc_cleanup_mode)

        self.is_synchronous = self.driver.driver_conf.is_synchronous
        if self.is_synchronous.lower() == "false":
            self.is_synchronous = False
        else:
            self.is_synchronous = True

    def create(self, context, obj):
        LOG.debug("%s, create %s", self.__class__.__name__, obj.id)
        try:
            self.create_entity(context, obj)
            if self.is_synchronous:
                self.successful_completion(context, obj)
            else:
                self.track_provision_status(obj)
        except:
            self.failed_completion(context, obj)
            LOG.exception(
                _LE("An exception occurred in client"))
            raise

    def update(self, context, old_obj, obj):
        LOG.debug("%s, update %s", self.__class__.__name__, old_obj.id)
        try:
            self.update_entity(context, old_obj, obj)
            if self.is_synchronous:
                self.successful_completion(context, obj)
            else:
                self.track_provision_status(obj)
        except Exception as e:
            self.failed_completion(context, obj)
            raise e

    def delete(self, context, obj):
        LOG.debug("%s, delete %s", self.__class__.__name__, obj.id)
        try:
            self.delete_entity(context, obj)
            if self.is_synchronous:
                self.successful_completion(context, obj, delete=True)
            else:
                self.track_provision_status(obj)
        except Exception as e:
            self.failed_completion(context, obj)
            raise e

    def track_provision_status(self, obj):
        for lb in self._get_loadbalancers(obj):
            if lb.id not in PROVISIONING_STATUS_TRACKER:
                PROVISIONING_STATUS_TRACKER.append(lb.id)

    def _get_loadbalancers(self, obj):
        lbs = []
        lbs.append(obj.root_loadbalancer)
        return lbs

    @abc.abstractmethod
    def create_entity(self, context, obj):
        pass

    @abc.abstractmethod
    def update_entity(self, context, old_obj, obj):
        pass

    @abc.abstractmethod
    def delete_entity(self, context, obj):
        pass


class NetScalerLoadBalancerManager(NetScalerCommonManager,
                                   driver_base.BaseLoadBalancerManager):

    def __init__(self, driver):
        driver_base.BaseLoadBalancerManager.__init__(self, driver)
        NetScalerCommonManager.__init__(self, driver)

    def refresh(self, context, lb_obj):
        # This is intended to trigger the backend to check and repair
        # the state of this load balancer and all of its dependent objects
        LOG.debug("LB refresh %s", lb_obj.id)

    def stats(self, context, lb_obj):
        pass

    def create_entity(self, context, lb_obj):
        ncc_lb = self.payload_preparer.prepare_lb_for_creation(lb_obj)
        vip_subnet_id = lb_obj.vip_subnet_id
        network_info = self.payload_preparer.\
            get_network_info(context, self.driver.plugin, vip_subnet_id)
        ncc_lb = dict(ncc_lb.items() + network_info.items())
        msg = _("NetScaler driver lb creation: %s") % repr(ncc_lb)
        LOG.debug(msg)
        resource_path = "%s/%s" % (RESOURCE_PREFIX, LBS_RESOURCE)
        self.client.create_resource(context.tenant_id, resource_path,
                                    LB_RESOURCE, ncc_lb)

    def update_entity(self, context, old_lb_obj, lb_obj):
        update_lb = self.payload_preparer.prepare_lb_for_update(lb_obj)
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, LBS_RESOURCE, lb_obj.id)
        msg = (_("NetScaler driver lb_obj %(lb_obj_id)s update: %(lb_obj)s") %
               {"lb_obj_id": old_lb_obj.id, "lb_obj": repr(lb_obj)})
        LOG.debug(msg)
        self.client.update_resource(context.tenant_id, resource_path,
                                    LB_RESOURCE, update_lb)

    def delete_entity(self, context, lb_obj):
        """Delete a loadbalancer on a NetScaler device."""
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, LBS_RESOURCE, lb_obj.id)
        msg = _("NetScaler driver lb_obj removal: %s") % lb_obj.id
        LOG.debug(msg)
        self.client.remove_resource(context.tenant_id, resource_path)


class NetScalerListenerManager(NetScalerCommonManager,
                               driver_base.BaseListenerManager):

    def __init__(self, driver):
        driver_base.BaseListenerManager.__init__(self, driver)
        NetScalerCommonManager.__init__(self, driver)

    def stats(self, context, listener):
        # returning dummy status now
        LOG.debug(
            "Tenant id %s , Listener stats %s", context.tenant_id, listener.id)
        return {
            "bytes_in": 0,
            "bytes_out": 0,
            "active_connections": 0,
            "total_connections": 0
        }

    def create_entity(self, context, listener):
        """Listener is created with loadbalancer """
        ncc_listener = self.payload_preparer.prepare_listener_for_creation(
            listener)
        msg = _("NetScaler driver listener creation: %s") % repr(ncc_listener)
        LOG.debug(msg)
        resource_path = "%s/%s" % (RESOURCE_PREFIX, LISTENERS_RESOURCE)
        self.client.create_resource(context.tenant_id, resource_path,
                                    LISTENER_RESOURCE, ncc_listener)

    def update_entity(self, context, old_listener, listener):
        update_listener = self.payload_preparer.prepare_listener_for_update(
            listener)
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, LISTENERS_RESOURCE,
                                      listener.id)
        msg = (_("NetScaler driver listener %(listener_id)s "
                 "update: %(listener_obj)s") %
               {"listener_id": old_listener.id,
                "listener_obj": repr(listener)})
        LOG.debug(msg)
        self.client.update_resource(context.tenant_id, resource_path,
                                    LISTENER_RESOURCE, update_listener)

    def delete_entity(self, context, listener):
        """Delete a listener on a NetScaler device."""
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, LISTENERS_RESOURCE,
                                      listener.id)
        msg = _("NetScaler driver listener removal: %s") % listener.id
        LOG.debug(msg)
        self.client.remove_resource(context.tenant_id, resource_path)


class NetScalerPoolManager(NetScalerCommonManager,
                           driver_base.BasePoolManager):

    def __init__(self, driver):
        driver_base.BasePoolManager.__init__(self, driver)
        NetScalerCommonManager.__init__(self, driver)

    def create_entity(self, context, pool):
        ncc_pool = self.payload_preparer.prepare_pool_for_creation(
            pool)
        msg = _("NetScaler driver pool creation: %s") % repr(ncc_pool)
        LOG.debug(msg)
        resource_path = "%s/%s" % (RESOURCE_PREFIX, POOLS_RESOURCE)
        self.client.create_resource(context.tenant_id, resource_path,
                                    POOL_RESOURCE, ncc_pool)

    def update_entity(self, context, old_pool, pool):
        update_pool = self.payload_preparer.prepare_pool_for_update(pool)
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, POOLS_RESOURCE,
                                      pool.id)
        msg = (_("NetScaler driver pool %(pool_id)s update: %(pool_obj)s") %
               {"pool_id": old_pool.id, "pool_obj": repr(pool)})
        LOG.debug(msg)
        self.client.update_resource(context.tenant_id, resource_path,
                                    POOL_RESOURCE, update_pool)

    def delete_entity(self, context, pool):
        """Delete a pool on a NetScaler device."""
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, POOLS_RESOURCE,
                                      pool.id)
        msg = _("NetScaler driver pool removal: %s") % pool.id
        LOG.debug(msg)
        self.client.remove_resource(context.tenant_id, resource_path)


class NetScalerMemberManager(NetScalerCommonManager,
                             driver_base.BaseMemberManager):

    def __init__(self, driver):
        driver_base.BaseMemberManager.__init__(self, driver)
        NetScalerCommonManager.__init__(self, driver)

    def create_entity(self, context, member):

        ncc_member = self.payload_preparer.prepare_member_for_creation(member)
        subnet_id = member.subnet_id
        network_info = (self.payload_preparer.
                        get_network_info(context, self.driver.plugin,
                                         subnet_id))
        ncc_member = dict(ncc_member.items() + network_info.items())
        msg = _("NetScaler driver member creation: %s") % repr(ncc_member)
        LOG.debug(msg)
        parent_pool_id = member.pool.id
        resource_path = "%s/%s/%s/%s" % (RESOURCE_PREFIX, POOLS_RESOURCE,
                                         parent_pool_id, MEMBERS_RESOURCE)
        self.client.create_resource(context.tenant_id, resource_path,
                                    MEMBER_RESOURCE, ncc_member)

    def update_entity(self, context, old_member, member):
        parent_pool_id = member.pool.id
        update_member = self.payload_preparer.prepare_member_for_update(member)
        resource_path = "%s/%s/%s/%s/%s" % (RESOURCE_PREFIX,
                                            POOLS_RESOURCE,
                                            parent_pool_id,
                                            MEMBERS_RESOURCE,
                                            member.id)
        msg = (_("NetScaler driver member %(member_id)s "
                 "update: %(member_obj)s") %
               {"member_id": old_member.id, "member_obj": repr(member)})
        LOG.debug(msg)
        self.client.update_resource(context.tenant_id, resource_path,
                                    MEMBER_RESOURCE, update_member)

    def delete_entity(self, context, member):
        """Delete a member on a NetScaler device."""
        parent_pool_id = member.pool.id
        resource_path = "%s/%s/%s/%s/%s" % (RESOURCE_PREFIX,
                                            POOLS_RESOURCE,
                                            parent_pool_id,
                                            MEMBERS_RESOURCE,
                                            member.id)
        msg = _("NetScaler driver member removal: %s") % member.id
        LOG.debug(msg)
        self.client.remove_resource(context.tenant_id, resource_path)


class NetScalerHealthMonitorManager(NetScalerCommonManager,
                                    driver_base.BaseHealthMonitorManager):

    def __init__(self, driver):
        driver_base.BaseHealthMonitorManager.__init__(self, driver)
        NetScalerCommonManager.__init__(self, driver)

    def create_entity(self, context, hm):
        ncc_hm = self.payload_preparer.prepare_healthmonitor_for_creation(hm)
        msg = _("NetScaler driver healthmonitor creation: %s") % repr(ncc_hm)
        LOG.debug(msg)
        resource_path = "%s/%s" % (RESOURCE_PREFIX, MONITORS_RESOURCE)
        self.client.create_resource(context.tenant_id, resource_path,
                                    MONITOR_RESOURCE, ncc_hm)

    def update_entity(self, context, old_healthmonitor, hm):
        update_hm = self.payload_preparer.prepare_healthmonitor_for_update(hm)
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, MONITORS_RESOURCE,
                                      hm.id)
        msg = (_("NetScaler driver healthmonitor %(healthmonitor_id)s "
                 "update: %(healthmonitor_obj)s") %
               {"healthmonitor_id": old_healthmonitor.id,
                "healthmonitor_obj": repr(hm)})
        LOG.debug(msg)
        self.client.update_resource(context.tenant_id, resource_path,
                                    MONITOR_RESOURCE, update_hm)

    def delete_entity(self, context, hm):
        """Delete a healthmonitor on a NetScaler device."""
        resource_path = "%s/%s/%s" % (RESOURCE_PREFIX, MONITORS_RESOURCE,
                                      hm.id)
        msg = _("NetScaler driver healthmonitor removal: %s") % hm.id
        LOG.debug(msg)
        self.client.remove_resource(context.tenant_id, resource_path)


class PayloadPreparer(object):

    def prepare_lb_for_creation(self, lb):
        creation_attrs = {
            'id': lb.id,
            'tenant_id': lb.tenant_id,
            'vip_address': lb.vip_address,
            'vip_subnet_id': lb.vip_subnet_id,
        }
        update_attrs = self.prepare_lb_for_update(lb)
        creation_attrs.update(update_attrs)

        return creation_attrs

    def prepare_lb_for_update(self, lb):
        return {
            'name': lb.name,
            'description': lb.description,
            'admin_state_up': lb.admin_state_up,
        }

    def prepare_listener_for_creation(self, listener):
        creation_attrs = {
            'id': listener.id,
            'tenant_id': listener.tenant_id,
            'protocol': listener.protocol,
            'protocol_port': listener.protocol_port,
            'loadbalancer_id': listener.loadbalancer_id
        }
        update_attrs = self.prepare_listener_for_update(listener)
        creation_attrs.update(update_attrs)
        return creation_attrs

    def prepare_listener_for_update(self, listener):
        sni_container_ids = self.prepare_sni_container_ids(listener)
        listener_dict = {
            'name': listener.name,
            'description': listener.description,
            'sni_container_ids': sni_container_ids,
            'default_tls_container_id': listener.default_tls_container_id,
            'connection_limit': listener.connection_limit,
            'admin_state_up': listener.admin_state_up
        }
        return listener_dict

    def prepare_pool_for_creation(self, pool):
        create_attrs = {
            'id': pool.id,
            'tenant_id': pool.tenant_id,
            'listener_id': pool.listener.id,
            'protocol': pool.protocol,
        }
        update_attrs = self.prepare_pool_for_update(pool)
        create_attrs.update(update_attrs)
        return create_attrs

    def prepare_pool_for_update(self, pool):
        update_attrs = {
            'name': pool.name,
            'description': pool.description,
            'lb_algorithm': pool.lb_algorithm,
            'admin_state_up': pool.admin_state_up
        }
        if pool.sessionpersistence:
            peristence = pool.sessionpersistence
            peristence_payload = self.prepare_sessionpersistence(peristence)
            update_attrs['session_persistence'] = peristence_payload
        return update_attrs

    def prepare_sessionpersistence(self, persistence):
        return {
            'type': persistence.type,
            'cookie_name': persistence.cookie_name
        }

    def prepare_members_for_pool(self, members):
        members_attrs = []
        for member in members:
            member_attrs = self.prepare_member_for_creation(member)
            members_attrs.append(member_attrs)
        return members_attrs

    def prepare_member_for_creation(self, member):
        creation_attrs = {
            'id': member.id,
            'tenant_id': member.tenant_id,
            'address': member.address,
            'protocol_port': member.protocol_port,
            'subnet_id': member.subnet_id
        }
        update_attrs = self.prepare_member_for_update(member)
        creation_attrs.update(update_attrs)
        return creation_attrs

    def prepare_member_for_update(self, member):
        return {
            'weight': member.weight,
            'admin_state_up': member.admin_state_up,
        }

    def prepare_healthmonitor_for_creation(self, health_monitor):
        creation_attrs = {
            'id': health_monitor.id,
            'tenant_id': health_monitor.tenant_id,
            'pool_id': health_monitor.pool.id,
            'type': health_monitor.type,
        }
        update_attrs = self.prepare_healthmonitor_for_update(health_monitor)
        creation_attrs.update(update_attrs)
        return creation_attrs

    def prepare_healthmonitor_for_update(self, health_monitor):
        ncc_hm = {
            'delay': health_monitor.delay,
            'timeout': health_monitor.timeout,
            'max_retries': health_monitor.max_retries,
            'admin_state_up': health_monitor.admin_state_up,
        }
        if health_monitor.type in ['HTTP', 'HTTPS']:
            ncc_hm['http_method'] = health_monitor.http_method
            ncc_hm['url_path'] = health_monitor.url_path
            ncc_hm['expected_codes'] = health_monitor.expected_codes
        return ncc_hm

    def get_network_info(self, context, plugin, subnet_id):
        network_info = {}
        subnet = plugin.db._core_plugin.get_subnet(context, subnet_id)
        network_id = subnet['network_id']
        network = plugin.db._core_plugin.get_network(context, network_id)
        network_info['network_id'] = network_id
        network_info['subnet_id'] = subnet_id
        network_info['subnet_name'] = subnet['name']
        if PROV_NET_TYPE in network:
            network_info['network_type'] = network[PROV_NET_TYPE]
        if PROV_SEGMT_ID in network:
            network_info['segmentation_id'] = network[PROV_SEGMT_ID]
        return network_info

    def prepare_sni_container_ids(self, listener):
        sni_container_ids = []
        for sni_container in listener.sni_containers:
            sni_container_ids.append(sni_container.tls_container_id)
        return sni_container_ids


class NetScalerStatusService(service.Service):

    def __init__(self, driver):
        super(NetScalerStatusService, self).__init__()
        self.driver = driver

    def start(self):
        super(NetScalerStatusService, self).start()
        try :
            self.tg.add_timer(
                int(self.driver.periodic_task_interval),
                self.driver.collect_provision_status,
                None
                
            )
        except :
            LOG.error("an exception happened in the thread")
            raise
