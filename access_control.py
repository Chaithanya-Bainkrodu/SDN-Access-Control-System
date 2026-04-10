from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4


class AccessControl(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(AccessControl, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

        # ✅ WHITELIST
        self.whitelist = {
            "10.0.0.1": ["10.0.0.2"],
            "10.0.0.2": ["10.0.0.1"],
	    "10.0.0.3": []
        }

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]

        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]

        mod = parser.OFPFlowMod(datapath=datapath,
                               priority=priority,
                               match=match,
                               instructions=inst)

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # ❗ Ignore LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        # Learn MAC
        self.mac_to_port[dpid][src] = in_port

        # Determine output port
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = []

        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        # ✅ ACCESS CONTROL LOGIC
        if ip_pkt:
            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst

            self.logger.info(f"{src_ip} -> {dst_ip}")

            match = parser.OFPMatch(
                in_port=in_port,
                eth_type=0x0800,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip
            )

            # ✅ ALLOW
            if src_ip in self.whitelist and dst_ip in self.whitelist[src_ip]:
                self.logger.info("ALLOWED")
                actions = [parser.OFPActionOutput(out_port)]

                # install allow rule
                self.add_flow(datapath, 10, match, actions)

            # ❌ BLOCK
            else:
                self.logger.info("BLOCKED")

                # install DROP rule
                self.add_flow(datapath, 10, match, [])

                return  # 🚨 stop forwarding

        else:
            # allow ARP and other non-IP packets
            actions = [parser.OFPActionOutput(out_port)]

        # send packet
        out = parser.OFPPacketOut(datapath=datapath,
                                 buffer_id=ofproto.OFP_NO_BUFFER,
                                 in_port=in_port,
                                 actions=actions,
                                 data=msg.data)

        datapath.send_msg(out)
