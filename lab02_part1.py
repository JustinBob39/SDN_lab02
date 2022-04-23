from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4, lldp
from ryu.base.app_manager import lookup_service_brick
from ryu.topology.api import get_switch, get_link
from ryu.topology.switches import LLDPPacket
from ryu.lib import hub
import networkx as nx
import time

GET_TOPOLOGY_INTERVAL = 4
SEND_ECHO_REQUEST_INTERVAL = .1
GET_DELAY_INTERVAL = 4


class Switch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(Switch, self).__init__(*args, **kwargs)

        # global data structure to save the mac_to_port[dpid][eth_src] = port
        self.mac_to_port = {}

        # global data structure to save the arp_in_port[dpid][arp_src_mac][arp_dst_ip] = port
        self.arp_in_port = {}

        # global data structure to save the topo map
        self.topo_map = nx.Graph()

        # switch connect switch
        # switch_switch[dpid][connected_switch_dpid] = port
        self.switch_switch = {}

        # switch connect host
        # switch_host[dpid][host_ip] = port
        self.switch_host = {}

        # all the datapath to hand out flow table entry
        # datapath[dpid] = datapath
        self.datapath = {}

        # all the switches for lookup_service_brick
        self.switches = None

        # store the lldp delay
        # lldp_dealy[(dpid, connected_switch_dpid)] = sec
        self.lldp_delay = {}

        # store the echo delay
        # echo_dealy[dpid] = sec
        self.echo_delay = {}

        # store the start timestamp of echo
        # echo_start[dpid] = timestamp
        self.echo_start = {}

        # thread to get the topo
        self.topo_thread = hub.spawn(self.get_topology)

        # thread to send echo
        self.echo_thread = hub.spawn(self.send_echo_request)

        # thread to get the delay
        self.delay_thread = hub.spawn(self.get_delay)

    # add a flow table entry in switch
    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        dp = datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # construct a FlowMod message
        # send to a switch to add a flow table entry
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=priority,
                                idle_timeout=idle_timeout,
                                hard_timeout=hard_timeout,
                                match=match, instructions=inst)
        dp.send_msg(mod)

    # set the table_miss entry
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions)

    # handle packet_in message
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath

        # the identity of switch
        dpid = dp.id

        # print('receive msg {}'.format(msg))

        # record the datapath for later add flow table entry
        self.datapath[dpid] = dp

        # make the value of mac_to_port dictionary
        self.mac_to_port.setdefault(dpid, {})

        # same above
        self.arp_in_port.setdefault(dpid, {})
        self.switch_host.setdefault(dpid, {})

        # use the msg.data to make a packet
        pkt = packet.Packet(msg.data)

        # extract the content of the packet of different protocols
        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        lldp_pkt = pkt.get_protocol(lldp.lldp)
        arp_pkt = pkt.get_protocol(arp.arp)
        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)

        # the port that receive the packet
        in_port = msg.match['in_port']

        # get the source mac address
        eth_src = eth_pkt.src

        # learn a mac and port relation avoid FLOOD
        if eth_src not in self.mac_to_port[dpid].keys():
            self.mac_to_port[dpid][eth_src] = in_port

        # if is lldp call function to handle
        if isinstance(lldp_pkt, lldp.lldp):
            # print('handle a lldp packet {}'.format(lldp_pkt))
            self.handle_lldp(lldp_pkt, msg)

        # if is arp call function to handle
        if isinstance(arp_pkt, arp.arp):
            print('handle an arp packet {}'.format(arp_pkt))
            self.handle_arp(arp_pkt, msg)

        # if is ipv4 call function to handle
        if isinstance(ipv4_pkt, ipv4.ipv4):
            print('handle an ipv4 packet {}'.format(ipv4_pkt))
            print('eth_src is {} and eth_dst is {}'.format(eth_src, eth_pkt.dst))
            self.handle_ipv4(ipv4_pkt, msg)

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def echo_reply_handler(self, ev):

        # calcualte the echo delay for every datapath
        dpid = ev.msg.datapath.id
        now = time.time()
        self.echo_delay[dpid] = now - self.echo_start[dpid]

        # print('echo_delay is {}'.format(self.echo_delay))

    def handle_lldp(self, lldp_pkt, msg):

        dpid = msg.datapath.id
        try:
            src_dpid, src_port_no = LLDPPacket.lldp_parse(msg.data)
        except LLDPPacket.LLDPUnknownFormat as e:
            # This handler can receive all the packtes which can be
            # not-LLDP packet. Ignore it silently
            print('receive a lldp unkown format')
            return

        if self.switches is None:
            self.switches = lookup_service_brick('switches')
            # print('lldp switches {}'.format(self.switches))

        for port in self.switches.ports.keys():
            if src_dpid == port.dpid and src_port_no == port.port_no:
                self.lldp_delay[(src_dpid, dpid)] = self.switches.ports[port].delay
                if src_dpid == 7 and dpid == 8:
                    print('lldp delay between 7 and 8 is {}'.format(self.lldp_delay[(src_dpid, dpid)]))

                # print('lldp delay between switch {} and switch {} is {}'
                #       .format(src_dpid, dpid, self.lldp_delay[(src_dpid, dpid)]))

    def handle_arp(self, arp_pkt, msg):

        # define the out port and eth_dst
        out_port = None
        eth_dst = None

        # get the dpid
        dpid = msg.datapath.id

        # get the ofp
        ofp = msg.datapath.ofproto

        # get the parser
        parser = msg.datapath.ofproto_parser

        # get in port
        in_port = msg.match['in_port']

        # learn a switch-host relation
        host = True
        for tmp in self.switch_switch[dpid].keys():

            # this is a switch_switch port
            if in_port == self.switch_switch[dpid][tmp]:
                host = False
                break

        # this is a switch_host port, learn it
        if host:
            arp_src_ip = arp_pkt.src_ip
            self.switch_host[dpid][arp_src_ip] = in_port

        # this is an arp request
        if arp_pkt.opcode == arp.ARP_REQUEST:

            # get the arp_dst_ip and arp_src_mac
            arp_dst_ip = arp_pkt.dst_ip
            arp_src_mac = arp_pkt.src_mac

            # if not exist the record then record
            # if exist the record then compare whether the same
            if arp_src_mac not in self.arp_in_port[dpid].keys():
                self.arp_in_port[dpid].setdefault(arp_src_mac, {})
                self.arp_in_port[dpid][arp_src_mac][arp_dst_ip] = in_port
            else:
                if arp_dst_ip not in self.arp_in_port[dpid][arp_src_mac].keys():
                    self.arp_in_port[dpid][arp_src_mac][arp_dst_ip] = in_port
                else:
                    if in_port != self.arp_in_port[dpid][arp_src_mac][arp_dst_ip]:
                        print('Drop an arp request to avoid loop storm.')
                        return
            out_port = ofp.OFPP_FLOOD

        # this is an arp response
        else:
            pkt = packet.Packet(msg.data)
            eth_pkt = pkt.get_protocol(ethernet.ethernet)
            eth_dst = eth_pkt.dst

            if eth_dst in self.mac_to_port[dpid].keys():
                out_port = self.mac_to_port[dpid][eth_dst]
            else:
                out_port = ofp.OFPP_FLOOD

        # deal with the packet going to send
        actions = [parser.OFPActionOutput(out_port)]

        # add a flow table entry to the switch
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth_dst)
            self.add_flow(msg.datapath, 10, match, actions, 90, 180)

        # send packet out to FLOOD the packet
        data = None
        if msg.buffer_id == ofp.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=msg.datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        msg.datapath.send_msg(out)

    def handle_ipv4(self, ipv4_pkt, msg):

        # get the ofp and the parser
        ofp = msg.datapath.ofproto
        parser = msg.datapath.ofproto_parser

        ipv4_src = ipv4_pkt.src
        ipv4_dst = ipv4_pkt.dst

        dpid_begin = None
        dpid_final = None

        port_begin = None
        port_final = None

        find_begin = False
        for dpid in self.switch_host.keys():
            for ip in self.switch_host[dpid].keys():
                if ip == ipv4_src:
                    port_begin = self.switch_host[dpid][ip]
                    dpid_begin = dpid
                    find_begin = True
                    break
            if find_begin:
                break

        find_final = False
        for dpid in self.switch_host.keys():
            for ip in self.switch_host[dpid].keys():
                if ip == ipv4_dst:
                    port_final = self.switch_host[dpid][ip]
                    dpid_final = dpid
                    find_final = True
                    break
            if find_final:
                break

        short_path = nx.dijkstra_path(self.topo_map, dpid_begin, dpid_final)
        min_delay = nx.dijkstra_path_length(self.topo_map, dpid_begin, dpid_final)
        print('nx find the shortest path {}, the min_delay is {}'.format(short_path, min_delay*1000))

        # print the path, get the switch and port
        # add flow table entry
        path = str(ipv4_src) + '-->' + str(port_begin) + ':' + str(dpid_begin)

        for i in range(0, len(short_path)):

            cur_switch = short_path[i]

            # the first switch
            if i == 0:
                next_switch = short_path[i + 1]
                port = self.switch_switch[cur_switch][next_switch]
                path = path + ':' + str(port) + '-->'

                # add flow table to the first switch
                # back
                out_port = port_begin
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_dst, ipv4_dst=ipv4_src)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

                # go
                out_port = self.switch_switch[cur_switch][next_switch]
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_src, ipv4_dst=ipv4_dst)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

            # the final switch
            elif i == len(short_path) - 1:
                pre_switch = short_path[i - 1]
                port = self.switch_switch[cur_switch][pre_switch]
                path = path + str(port) + ':' + str(cur_switch)

                # add flow table to the final switch
                # back
                out_port = port
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_dst, ipv4_dst=ipv4_src)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

                # go
                out_port = port_final
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_src, ipv4_dst=ipv4_dst)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

            else:
                pre_switch = short_path[i - 1]
                next_switch = short_path[i + 1]
                port1 = self.switch_switch[cur_switch][pre_switch]
                port2 = self.switch_switch[cur_switch][next_switch]
                path = path + str(port1) + ':' + str(cur_switch) + ':' + str(port2) + '-->'

                # add flow table to the middle switch
                # back
                out_port = port1
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_dst, ipv4_dst=ipv4_src)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

                # go
                out_port = port2
                actions = [parser.OFPActionOutput(out_port)]
                match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_src, ipv4_dst=ipv4_dst)
                self.add_flow(self.datapath[cur_switch], 20, match, actions, 300, 600)

        path = path + ':' + str(port_final) + '-->' + str(ipv4_dst)
        print(path)

        # send packet out to the first switch
        out_port = self.switch_switch[short_path[0]][short_path[1]]
        actions = [parser.OFPActionOutput(out_port)]
        data = None
        if msg.buffer_id == ofp.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=msg.datapath, buffer_id=msg.buffer_id,
                                  in_port=msg.match['in_port'], actions=actions, data=data)
        msg.datapath.send_msg(out)

    def get_topology(self):
        while True:

            # get all the switch
            switch_list = get_switch(self)
            for switch in switch_list:
                self.switch_switch.setdefault(switch.dp.id, {})

            # get all the link
            link_list = get_link(self)
            for link in link_list:
                self.switch_switch[link.src.dpid][link.dst.dpid] = link.src.port_no
                self.topo_map.add_edge(link.src.dpid, link.dst.dpid)

            # print('get_topology thread done!')
            hub.sleep(GET_TOPOLOGY_INTERVAL)

    def send_echo_request(self):
        while True:

            # send echo request to every datapath and record the start timestamp
            for dp in self.datapath.values():
                data = None
                parser = dp.ofproto_parser
                req = parser.OFPEchoRequest(dp, data)
                dp.send_msg(req)

                self.echo_start[dp.id] = time.time()

            # print('send_echo_request thread done!')
            hub.sleep(SEND_ECHO_REQUEST_INTERVAL)

    def get_delay(self):
        while True:

            # calculate the final delay and add to the topo_map as weight
            for edge in self.topo_map.edges:
                weight = (self.lldp_delay[(edge[0], edge[1])] + self.lldp_delay[(edge[1], edge[0])]
                          - self.echo_delay[edge[0]] - self.echo_delay[edge[1]]) / 2
                # print('dealy between switch {} and switch {} is {}'.format(edge[0], edge[1], weight))

                if weight < 0:
                    weight = 0

                # G[source][target]['weight'] = weight
                self.topo_map[edge[0]][edge[1]]['weight'] = weight


            print('get_dealy thread done!')
            hub.sleep(GET_DELAY_INTERVAL)
