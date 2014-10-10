# Copyright (c) 2014 Alcatel-Lucent Enterprise
# All Rights Reserved.
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

import time, os, sys
try:
    import yaml
except:
    print "Module yaml not found !"
    print "sudo pip install pyyaml"
    sys.exit(1)
try:
    import novaclient.client, cinderclient.client
except:
    print "Module nova and/or cinder not found !"
    print "sudo pip install python-novaclient"
    print "sudo pip install python-cinderclient"
    sys.exit(1)
try:
    from neutronclient.v2_0 import client as neutronClient
except:
    print "sudo pip install python-neutronclient"
    sys.exit(1)
try:
    import ansible.playbook
    from ansible import callbacks
    from ansible import utils
except:
    print "Module ansible not found !"
    print "sudo pip install ansible"
    sys.exit(1)

# Used for ansible inventory
import ConfigParser

import subprocess
import shlex
# Used for playbooks stdout
from threading  import Thread

class CommandExecutor:
    def __init__(self, verbose):
        self.verbose = verbose

    def command(self, cmd, async=False, environment=None):
        to_execute = cmd
        env = os.environ.copy()
        if environment:
            env.update(environment)
        if self.verbose:
            print env
            print ">>"+to_execute
        if async:
            return subprocess.Popen(shlex.split(to_execute), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, universal_newlines=True)
        else:
            return subprocess.call(shlex.split(to_execute), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, universal_newlines=True)

    def isRunning(self, p):
        return p.poll() is None


class Volume:
    def __init__(self, name, size):
        self.name = name
        self.size = size


class InstanceEvents:
    def onVMCreated(self, instance):
        pass
    def onVMActive(self, instance):
        pass
    def onVMReady(self, instance):
        pass
    def onVMDeleted(self, instance):
        pass


class Instance:
    def __init__(self, name, flavor, ansible_sections, playbook_file, volumes, floating_ips, vips, additional_security_groups, dependencies, nova, cinder, neutron, image, networks, ssh_key_name, ssh_user, ssh_key, cmdExe, static):
        self.name = name
        self.flavor = flavor
        self.ansible_sections = ansible_sections
        self.playbook_file = playbook_file
        self.volumes = volumes
        self.floating_ips = floating_ips
        self.vips = vips
        self.additional_security_groups = additional_security_groups
        self.dependencies = dependencies
        self.nova = nova
        self.cinder = cinder
        self.neutron = neutron
        self.image = image
        self.networks = networks
        self.ssh_key_name = ssh_key_name
        self.ssh_key = ssh_key
        self.ssh_user = ssh_user
        self.cmdExe = cmdExe
        self.vm = None
        self.deploy_dependencies = []
        self.callbacks = []
        self.status = "Unavailable"
        self.static = static
        self.checkReady = False

    def updateVM(self, vm):
        previous_vm = self.vm
        self.vm = vm
        if vm:
            if not previous_vm:
                self.status = "Created"
                for c in self.callbacks:
                    c.onVMCreated(self)
            if self.status == "Created":
                if self.vm.status == "ACTIVE":
                    self.status = "Active"
                    for c in self.callbacks:
                        c.onVMActive(self)
            if self.vm.status == "ACTIVE" and not self.status == "Ready" and self.checkReady:
                if self.cmdExe.command("ssh -o LogLevel=quiet -o ConnectTimeout=2 -o StrictHostKeychecking=no -o UserKnownHostsFile=/dev/null -i " + self.ssh_key.replace(" ", "\ ") + " " + self.ssh_user + "@" + self.getIPAddress() + " \"ls && ! pgrep apt-get\"") == 0:
                    self.status = "Ready"
                    for c in self.callbacks:
                        c.onVMReady(self)
        else:
            if previous_vm:
                self.status = "Deleted"
                for c in self.callbacks:
                    c.onVMDeleted(self)

    def getPortId(self, ip):
         ports = self.neutron.list_ports()
         for k in ports["ports"]:
            if (k["fixed_ips"][0]["ip_address"] == ip):
                return k["id"]

    def getFloatingIpId(self, ip):
        floatingips = self.neutron.list_floatingips()
        for k in floatingips["floatingips"]:
            if k["floating_ip_address"] == ip:
                return k["id"]

    def getNetworkId(self, name):
        networks = self.neutron.list_networks()
        for k in networks["networks"]:
            if k["name"] == name:
                return k["id"]

    def getIPAddress(self, net=0):
        if self.vm and self.vm.status == "ACTIVE" and self.vm.networks.has_key(self.networks[net]):
            return self.vm.networks[self.networks[net]][0]
        return None

    def getSecurityGroupId(self, name):
        list = self.nova.security_groups.list()
        i = 0
        for k in list:
            if k.name == name:
                return k.id
            i = i + 1
        return None

    def delete(self):
        if self.static:
            raise RuntimeError("Not allowed to delete static VM")
        if not self.vm:
            return

        self.checkReady = False
        # delete ports for vips
        if len(self.vips) > 0 and self.floating_ips > 0:
            for vip in self.vips:
                portId = self.getPortId(vip)
                if portId != None:
                    self.neutron.delete_port(portId)


        # Get attached volumes
        l = []
        for v in self.vm._info['os-extended-volumes:volumes_attached']:
            l.append(v["id"])
        self.nova.servers.delete(self.vm)
        # Destroy attached volumes
        for v in l:
            while len(self.cinder.volumes.get(v)._info["attachments"]):
                time.sleep(1)
            print "Destroying %s volume"%v
            self.cinder.volumes.delete(v)

    def create(self):
        if self.static:
            raise RuntimeError("Not allowed to create static VM")
        self.checkReady = True
        block_device_mapping = {}
        if len(self.volumes):
            letter = ord('b')
            for vol in self.volumes:
                print "Creating volume %s (%i GB)"%(vol.name, vol.size)
                v = self.cinder.volumes.create(display_name=vol.name, size=vol.size)
                block_device_mapping["/dev/vd%c"%chr(letter)] = "%s:::0"%v.id
                letter= letter + 1
                # Wait for creation
                created = False
                while not created:
                    v = self.cinder.volumes.get(v.id)
                    #print v.status
                    if v.status == "available":
                        created = True
                    time.sleep(1)
        flavor = self.nova.flavors.find(name=self.flavor)
        nics = []
        for network in self.networks:
            net = self.nova.networks.find(label=network)
            nics.append({'net-id': net.id})
        img = self.nova.images.find(name=self.image)
        self.nova.servers.create(self.name, img, flavor, nics=nics, key_name = self.ssh_key_name, block_device_mapping=block_device_mapping)

    def whenReady(self):
        if self.static:
            raise RuntimeError("Not allowed to update static VM")
        self.associateFloatingIP()
        self.setSecurityGroups()
        self.cmdExe.command("ssh-keygen -R " + self.getIPAddress())
        name = self.name.split("-")
        vmType = name[1]
        #vip management
        
        i = 0
        if len(self.vips) > 0:
            # create port
            for vip in self.vips:
                if self.getPortId(vip) == None:
                    self.createPortForVip(i, vip)
                    i = i + 1

            # ip associating
            i = 0
            for floatingip in self.floating_ips:
                floatingipid = self.getFloatingIpId(floatingip)
                vipPortId = self.getPortId(self.vips[i])
                self.associateFloatingToPortVip(floatingipid, vipPortId)
                i = i + 1

            # update port
            i = 0
            for vip in self.vips:
                body = {
                            "port": {
                                "allowed_address_pairs" : [{
                                    "ip_address" : vip
                                }]
                            }
                }
                self.neutron.update_port(self.getPortId(self.getIPAddress(net=i)), body=body)
                i = i + 1

    def associateFloatingIP(self):
        if len(self.vips) == 0 and len(self.floating_ips):
            if not self.vm:
                raise RuntimeError("Error: could not associate floating IP on not existing VM %s"%self.name)
            cpt = 0
            for floating_ip in self.floating_ips:
                self.vm.add_floating_ip(floating_ip, self.getIPAddress(cpt))
                cpt = cpt + 1

    def associateFloatingToPortVip(self, floatingipId, portId):
        body = {
                "floatingip": {
                    "port_id" : portId
                }
        }
        self.neutron.update_floatingip(floatingipId, body=body)


    def setSecurityGroups(self):
        if len(self.additional_security_groups):
            if not self.vm:
                raise RuntimeError("Error: could not set security groups on not existing VM %s"%self.name)
            for sec in self.additional_security_groups:
                self.vm.add_security_group(sec)

    def createPortForVip(self,id, ip):
        securityGroupsIds = []
        for sec in self.additional_security_groups:
            group_id = self.getSecurityGroupId(sec)
            if group_id is None:
                raise RuntimeError("Error: could not find the security group id for %s"%sec)
            securityGroupsIds.append(str(group_id))

        body = {
            "port":
            {
                "admin_state_up": True,
                "name": "vip" + str(id),
                "network_id": self.getNetworkId(self.networks[id]),
                "fixed_ips" :
                [{
                 "ip_address" : ip
                 }],
                "security_groups" : securityGroupsIds
            }
        }
        self.neutron.create_port(body=body)


class PlaybookEvents:
    def onPlaybookUpdated(self, playbook):
        pass
    def onPlaybookCompleted(self, playbook):
        pass
    def onPlaybookError(self, playbook):
        pass

class Playbook:
    def __init__(self, name, path, dependencies, env, ssh_user, ssh_key, inventory_file, cmdExe, verbose, static):
        self.name = name
        self.path = path
        self.dependencies = dependencies
        self.env = env
        self.instances = []
        self.ssh_key = ssh_key
        self.ssh_user = ssh_user
        self.inventory_file = inventory_file
        self.cmdExe = cmdExe
        self.verbose = ""
        if verbose:
            self.verbose = "-vvvv"
        self.depPriority = 0
        self.process = None
        self.console_output=""
        self.callbacks = []
        self.status = "Not played"
        if static:
            self.status = "Not playable"
        self.current_task = 0

    def prepare(self):
        if self.status == "Not playable":
            raise RuntimeError("Not allowed to play %s"%self.name)

        self.current_task = 0
        self.tasks = []
        playbook_cb = callbacks.PlaybookCallbacks(verbose=utils.VERBOSITY)
        stats = callbacks.AggregateStats()
        runner_cb = callbacks.PlaybookRunnerCallbacks(stats, verbose=utils.VERBOSITY)
        pb = ansible.playbook.PlayBook(playbook=self.path, inventory=ansible.inventory.Inventory(self.inventory_file), remote_user=self.ssh_user, callbacks=playbook_cb, runner_callbacks=runner_cb, stats=stats, sudo="1", extra_vars={"env": self.env})
        for (play_ds, play_basedir) in zip(pb.playbook, pb.play_basedirs):
            play = ansible.playbook.Play(pb, play_ds, play_basedir,vault_password=pb.vault_password)
            label = play.name
            for task in play.tasks():
                if (set(task.tags).intersection(pb.only_tags) and not set(task.tags).intersection(pb.skip_tags)):
                    if getattr(task, 'name', None) is not None:
                        self.tasks.append(task.name)
        self.status = "Not played"
        self.priority = self.depPriority + len(self.tasks)

    def play(self):
        if self.status == "Not playable":
            raise RuntimeError("Not allowed to play %s"%self.name)
        self.status = "Running"
        # Start sub process
        self.process = self.cmdExe.command("ansible-playbook %s --sudo --user=%s --private-key=%s --extra-vars=env=%s --inventory-file=%s %s"%(self.verbose, self.ssh_user, self.ssh_key.replace(" ", "\ "), self.env, self.inventory_file.replace(" ", "\ "), self.path.replace(" ", "\ ")), True, environment={"ANSIBLE_HOST_KEY_CHECKING": "False", "PYTHONUNBUFFERED": "True"})

        t = Thread(target=self.processOutput)
        t.start()

    def terminate(self):
        self.process.terminate()
    
    def processOutput(self):
        for line in iter(self.process.stdout.readline,''):
            # Keep output in case of error
            self.console_output = self.console_output + line
            if line.startswith("TASK: "):
                self.current_task = self.current_task + 1
                for c in self.callbacks:
                    c.onPlaybookUpdated(self)
        while self.cmdExe.isRunning(self.process):
            time.sleep(1)
        if self.process.returncode == 0:
            self.status = "Completed"
            for c in self.callbacks:
                c.onPlaybookCompleted(self)
        else:
            self.status = "Error"
            for c in self.callbacks:
                c.onPlaybookError(self)

class TopologyEvents:
    # Provisioning events
    def onPlaybookAdded(self, playbook):
        pass
    def onPlaybookRemoved(self, playbook):
        pass
    def onInstanceAdded(self, instance):
        pass
    def onInstanceRemoved(self, instance):
        pass
    # Topology related events
    def onStarted(self):
        pass
    # Instance related events
    def onRedeployStarted(self):
        pass
    def onInstanceDeleted(self, instance):
        pass
    def onInstanceCreated(self, instance):
        pass
    def onInstanceActive(self, instance):
        pass
    def onInstanceReady(self, instance):
        pass
    def onAllInstancesReady(self):
        pass
    # Inventory related event
    def onInventoryGenerated(self):
        pass
    # Playbook related events
    def onAllPlaybooksStarted(self):
        pass
    def onPlaybookUpdated(self, playbook):
        pass
    def onPlaybookCompleted(self, playbook):
        pass
    def onAllPlaybooksCompleted(self):
        pass
    def onPlaybookError(self, playbook):
        pass


class Topology(PlaybookEvents, InstanceEvents):
    def __init__(self, topofile, verbose):
        self.instances = []
        self.playbooks = []
        # Open topology file
        with open(topofile, "r") as f:
            topology = yaml.load(f)[0]

        self.topo_directory = os.path.join(os.path.abspath(os.path.dirname(topofile)))

        # Parse topology
        # Generic values
        self.env = topology["globals"]["env"]
        self.ssh_key = os.path.join(self.topo_directory, topology["globals"]["ssh_key"])
        self.ssh_user = topology["globals"]["ssh_user"]
        self.ansible_inventory_template = os.path.join(self.topo_directory, topology["globals"]["ansible_inventory_template"])
        self.inventory_file = os.path.join(os.path.abspath(os.path.dirname(self.ansible_inventory_template)), self.env)
        
        self.cmdExe =  CommandExecutor(verbose)

        # Open stack variables
        os_user = topology["globals"]["os_user"]
        os_passwd = topology["globals"]["os_passwd"]
        os_tenant = topology["globals"]["os_tenant"]
        os_auth_url = topology["globals"]["os_auth_url"]
        os_image = topology["globals"]["os_image"]
        os_network = topology["globals"]["os_network"]
        os_ssh_key = topology["globals"]["os_ssh_key"]

        self.nova = novaclient.client.Client(2, os_user, os_passwd, os_tenant, os_auth_url)
        cinder = cinderclient.client.Client('1', os_user, os_passwd, os_tenant, os_auth_url)
        neutron = neutronClient.Client(username=os_user, password=os_passwd, tenant_name=os_tenant, auth_url=os_auth_url)

        # Nodes to initiate
        for i in topology["nodes"]:
            node = i["node"]
            name = node["name"]
            playbook = None
            if "playbook" in node:
                playbook = node["playbook"]
            flavor = None
            if "flavor" in node:
                flavor = node["flavor"]
            ansible_config_keys = []
            if "ansible_config_keys" in node:
                ansible_config_keys = [x.strip() for x in node["ansible_config_keys"].split(",")]
            volumes = []
            if "volumes" in node:
                for v in node["volumes"]:
                    volumes.append(Volume(v["name"], int(v["size"])))
            floating_ips = []
            if "floating_ips" in node:
                floating_ips = [x.strip() for x in node["floating_ips"].split(",")]
            
            vips = []
            if "vips" in node:
                vips = [x.strip() for x in node["vips"].split(",")]

            additional_security_groups = []
            if "security" in node:
                additional_security_groups = [x.strip() for x in node["security"].split(",")]
            dependencies = []
            if "depends" in node:
                dependencies = [x.strip() for x in node["depends"].split(",")]
            networks = [os_network]
            if "additional_network" in node:
                networks.extend([x.strip() for x in node["additional_network"].split(",")])
            static = False
            if not playbook or "static" in node:
                static = True
            instance = Instance(name, flavor, ansible_config_keys, playbook, volumes, floating_ips, vips, additional_security_groups, dependencies, self.nova, cinder, neutron, os_image, networks, os_ssh_key, self.ssh_user, self.ssh_key, self.cmdExe, static)
            instance.callbacks.append(self)
            self.instances.append(instance)
        f.close()

        # Compute playbooks
        self.ansible_playbooks_directory = os.path.join(self.topo_directory, topology["globals"]["ansible_playbooks_directory"])


        for instance in self.instances:
            if instance.playbook_file:
                pb = self.findPlaybook(instance.playbook_file)
                if pb:
                    pb.instances.append(instance)
                    for dep in instance.dependencies:
                        if not dep in pb.dependencies:
                            pb.dependencies.append(dep)
                else:
                    path = os.path.join(self.ansible_playbooks_directory, instance.playbook_file)
                    if not path.endswith(".yml"):
                        path = path + ".yml"
                    if not os.path.isfile(path):
                        raise NameError("Playbook %s does not exist! (no file %s)"%(instance.playbook_file, path))
                    pb = Playbook(instance.playbook_file, path, instance.dependencies, self.env, self.ssh_user, self.ssh_key, self.inventory_file, self.cmdExe, verbose, instance.static)
                    pb.instances.append(instance)
                    pb.callbacks.append(self)
                    self.playbooks.append(pb)

        # Check dependencies and compute a priority for each
        for pb in self.playbooks:
            for dep in pb.dependencies:
                p = self.findPlaybook(dep)
                if p:
                    p.depPriority = p.depPriority + 100
                else:
                    raise NameError("Dependency %s not defined in %s"%(dep, topofile))

        self.callbacks = []

        self.playbooks_to_play = []
        self.instances_to_redeploy = []
        self.is_running = False
        self.refreshInstances()
        self.refreshTime = 20
        t = Thread(target=self.refreshInstancesThread)
        t.setDaemon(True)
        t.start()

    def refreshInstancesThread(self):
        while True:
            time.sleep(self.refreshTime)
            self.refreshInstances()

    def refreshInstances(self):
        remainingTopoVMs = list(self.instances)
        vms = self.nova.servers.list()
        for vm in vms:
            instance = self.findInstance(vm.name)
            if instance:
                instance.updateVM(vm)
                if not instance in remainingTopoVMs:
                    raise RuntimeError("Duplicated VM with name %s"%instance.name)
                remainingTopoVMs.remove(instance)
        # Handle deleted VMs
        for i in remainingTopoVMs:
            i.updateVM(None)

    def onVMCreated(self, instance):
        for c in self.callbacks:
            c.onInstanceCreated(instance)

    def onVMActive(self, instance):
        for c in self.callbacks:
            c.onInstanceActive(instance)

    def onVMReady(self, instance):
        for c in self.callbacks:
            c.onInstanceReady(instance)
        if self.is_running and instance in self.instances_to_redeploy:
            instance.whenReady()
            self.instances_ready.append(instance)
            if len(self.instances_ready) == len(self.instances_to_redeploy):
                #Reset refresh timer
                self.refreshTime = 20
                for c in self.callbacks:
                    c.onAllInstancesReady()
                self.startPlaybooks()

    def onVMDeleted(self, instance):
        for c in self.callbacks:
            c.onInstanceDeleted(instance)
        if self.is_running and instance in self.instances_to_redeploy:
	    # sleep 5 seconds before recreating the VM to workaroud a neutron-dhcp-agent issue (DHCP not able to assign the IP if the VM is created directly after been deleted...)
	    time.sleep(5)
            instance.create()

    def startRedeployInstances(self):
        for c in self.callbacks:
            c.onRedeployStarted()
        self.instances_ready = []
        # Refresh time about status of VM
        self.refreshTime = 1
        for instance in self.instances_to_redeploy:
            # Do not create/delete static instances
            if not instance.static:
                if instance.vm:
                    instance.delete()
                else:
                    instance.create()

    def findInstance(self, name):
        for i in self.instances:
            if i.name == name:
                return i
        return None

    def generateAnsibleInventory(self):
        # Load template file
        conf = ConfigParser.RawConfigParser(allow_no_value=True)
        conf.read(os.path.join(self.ansible_inventory_template))

        # Add IPs in ansible configuration file
        for instance in self.instances:
            for section in instance.ansible_sections:
                ip = instance.getIPAddress()
                if ip == None:
                    raise RuntimeError("Could not generate inventory because \"%s\" not found (no IP, is it started ?)"%instance.name)
                host = ip + " name=" + instance.name
                if len(instance.floating_ips):
                    host = host + " public=" + instance.floating_ips[0]
                conf.set(section, host, None)
        f = open(self.inventory_file, "w")
        conf.write(f)
        f.close()
        for c in self.callbacks:
            c.onInventoryGenerated()

    
    def findPlaybook(self, name):
        for p in self.playbooks:
            if p.name == name:
                return p
        return None

    def playNextPlaybooks(self):
        candidates = []
        cpt_running = 0
        for pb in self.playbooks_to_play:
            if pb.status == "Not played":
                # Check dependencies
                all_dep_ok = True
                for d in pb.dependencies:
                    pbDep = self.findPlaybook(d)
                    if pbDep in self.playbooks_to_play and pbDep.status != "Completed":
                        all_dep_ok = False
                if all_dep_ok:
                    candidates.append(pb)
            elif pb.status == "Running":
                cpt_running = cpt_running + 1
        # Takes candidate with higher priority
        def getPriority(pb):
            return pb.priority
        ordered_candidates = sorted(candidates, key=getPriority, reverse=True)
        if len(ordered_candidates):
            # Limit number of parallel playbooks
            nb = min(len(ordered_candidates), 10-cpt_running)
            for pb in ordered_candidates[:nb]:
                pb.play()

    def onPlaybookUpdated(self, playbook):
        for c in self.callbacks:
            c.onPlaybookUpdated(playbook)

    def onPlaybookCompleted(self, playbook):
        for c in self.callbacks:
            c.onPlaybookCompleted(playbook)
        self.completed_playbooks.append(playbook)
        if len(self.completed_playbooks) == len(self.playbooks_to_play):
            self.is_running = False
            for c in self.callbacks:
                c.onAllPlaybooksCompleted()
        else:
            self.playNextPlaybooks()

    def onPlaybookError(self, playbook):
        self.is_running = False
        for pb in self.playbooks_to_play:
            if pb.status == "Running":
                pb.terminate()
        for c in self.callbacks:
            c.onPlaybookError(playbook)

    def startPlaybooks(self):
        self.generateAnsibleInventory()
        
        # Prepare playbooks to play
        for pb in self.playbooks_to_play:
            pb.prepare()

        for c in self.callbacks:
            c.onAllPlaybooksStarted()

        self.end = False
        self.counter = 0
        self.completed_playbooks = []
        
        # Launch playbooks
        self.playNextPlaybooks()


    def addToRedeploy(self, name):
        if self.is_running:
            raise RuntimeError("Could modify because running")
        l = []
        i  = self.findInstance(name)
        if i:
            l.append(i)
        else:
            p = self.findPlaybook(name)
            if p:
                l = p.instances
            else:
                raise NameError("No instance or playbook named %s in topology"%name)
        for i in l:
            if not i in self.instances_to_redeploy:
                self.instances_to_redeploy.append(i)
                for c in self.callbacks:
                    c.onInstanceAdded(i)
                self.addToReconfigure(i.name)


    def removeToRedeploy(self, name):
        if self.is_running:
            raise RuntimeError("Could modify because running")
        l = []
        i  = self.findInstance(name)
        if i:
            l.append(i)
        else:
            p = self.findPlaybook(name)
            if p:
                l = p.instances
            else:
                raise NameError("No instance or playbook named %s in topology"%name)
        for i in l:
            if i in self.instances_to_redeploy:
                self.instances_to_redeploy.remove(i)
                for c in self.callbacks:
                    c.onInstanceRemoved(i)

    def addToReconfigure(self, name):
        if self.is_running:
            raise RuntimeError("Could modify because running")
        p = self.findPlaybook(name)
        if not p:
            i  = self.findInstance(name)
            if i:
                p = self.findPlaybook(i.playbook_file)
            else:
                raise NameError("No instance or playbook named %s in topology"%name)
        if not p in self.playbooks_to_play:
            self.playbooks_to_play.append(p)
            for c in self.callbacks:
                c.onPlaybookAdded(p)

    def removeToReconfigure(self, name):
        if self.is_running:
            raise RuntimeError("Could modify because running")
        p = self.findPlaybook(name)
        if not p:
            i  = self.findInstance(name)
            if i:
                p = self.findPlaybook(i.playbook_file)
            else:
                raise NameError("No instance or playbook named %s in topology"%name)
        if p in self.playbooks_to_play:
            self.playbooks_to_play.remove(p)
            for c in self.callbacks:
                c.onPlaybookRemoved(p)
            self.removeToRedeploy(p.name)

    def run(self):
        if self.is_running:
            raise RuntimeError("Could not run because already running")
        if len(self.instances_to_redeploy) or len(self.playbooks_to_play):
            self.is_running = True
            for c in self.callbacks:
                c.onStarted()
            if len(self.instances_to_redeploy):
                self.startRedeployInstances()
            else:
                self.startPlaybooks()

