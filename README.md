# SDN实验二

基于跳数的最短路径，我在实验一的附加中方案二 ARP代理 那种方法就做过了

原理非常类似，因此我就没有重复

因为我偏向于自己写代码，所以就没有用老师给的框架，我在自己第一次实验的基础上进行功能的增强

我运营的 ARPANET 越来越强，同时我的 SDN 编程能力也越来越强！



代码比较长，我的注释已经非常详细了，因此写报告的时候，我不会贴很多的代码，大致讲一下思路

代码唯一的缺点就是，冗余，有的功能可以打包进一个函数，我比较懒，就没想重构



## Part 0

### LLDP

在 Packet Tracer 上搭建图中的拓扑，关闭所有的 cdp，因为这是 Cisco 公司的软件

在所有的设备上开启全局 lldp，当然，也可以只在某个特定的端口开启 lldp

![image-20220414201258474](https://cdn.justinbob.site/typora/202204142012537.png)



查看最右边连接着三个交换机的那个交换机的 lldp，`show lldp neighbors`

可以看到信息很丰富，每条链路都有一个项，包括 Device ID 对方设备的名称、Local Intf 本地的接口、Hold_time 信息的维持时间、Capability 对方的能力、Port ID 对方的接口

![image-20220414201232578](https://cdn.justinbob.site/typora/202204142012518.png)



### 测量延迟

首先讲一下 SDN 如何利用 LLDP 测量链路时延，其实这部分才是本次实验最复杂的点，但是好在老师已经给出了方法，非常巧妙

修改 `ryu/ryu/topology/switches.py` 中的 Switch 类

![image-20220413130311316](https://cdn.justinbob.site/typora/202204131303252.png)



![image-20220413131119833](https://cdn.justinbob.site/typora/202204131311862.png)

修改完毕，重新编译安装

`cd ryu`

`sudo python3 setup.py install`



首先 Controller 构造一个 LLDP 数据包，打上时间戳 send_timestamp，S1 从 Controller 收到 LLDP 数据包后会向指定端口转发，S2 预先安装了流表项，收到 LLDP 数据包后，会转发给 Controller，Controller 收到后得到当时的时间戳 recv_timestamp，recv_timestamp - send_timestamp 就是红色箭头过程的延迟

S2 转发 LLDP 数据包的时候，会携带自己的 ingress 端口信息，Controller 结合 LLDP 数据包的内容和 S2 的端口信息，就能得到一个链路 link 的信息，这就是 OFDP 的大致原理

![image-20220413203236404](https://cdn.justinbob.site/typora/202204132032569.png)

`01:80:C2:00:00:0E` 是 LLDP 的目的 mac 地址，第一个 byte 的最低有效比特 LSB 为 1，表示是多播地址。包被限制在本地网络中，无法被任何桥或路由设备转发



这是 Switch 发送的 LLDP 数据包打上时间戳 send_timestamp 的过程

计算过程上面的截图中有了，就是两个相减，存到源端口的 PortData 的 delay 中，Port 是键，PortData是值

后面需要用的时候，直接读取就行

![image-20220413130745616](https://cdn.justinbob.site/typora/202204131313494.png)



Controller 会定时向各个交换机发送 echo request 报文，发送的时刻记为 start_timstamp，交换机收到后会立马响应，当 Controller 收到响应时，时刻为 end_timestamp，end_timestamp - start_timestamp 就是 Controller 和交换机之间的 RTT



因此

S1 到 S2 的时延 delay = (lldp_delay_s12 + lldp_delay_s21 - echo_delay_s1 - echo_delay_s2) / 2

![image-20220412224029594](https://cdn.justinbob.site/typora/202204122240482.png)





### 网络拓扑图

![ARPANET](https://cdn.justinbob.site/typora/202204122254550.jpg)

​														图中有环路，实验一中解决广播风暴部分的代码需要移植过来



## Part 1

### 实验思路

* 定义几个 hub 线程

    其实代码框架中，这些变量已经暗示地很明显了，我稍微调了一下参数

    ```python
    GET_TOPOLOGY_INTERVAL = 4
    SEND_ECHO_REQUEST_INTERVAL = .1
    GET_DELAY_INTERVAL = 4
    ```

    一个用来获取、更新网络拓扑

    一个用来向各个交换机发送 echo request 报文

    一个用来计算线路时延

* 在 Packet_In 中，对不同的数据包种类用不同的函数进行处理

    handle_lldp，负责从端口中提取出 lldp_delaySxy

    handle_arp，负责处理 ARP 请求和响应，主要是防止形成广播风暴

    handle_ipv4，负责计算最短路径，在路径上下发流表

* 用怎样的数据结构进行存储呢？

    首先实验一中使用的两个重要的结构我们可以继承，主要是 mac 地址和端口对应关系，还有防止 ARP 广播风暴

    网络拓扑图，可以用 networkx 构建有权图 Weighted Graph 进行存储

    Controller 和 Switch 之间的往返时延，可以用字典来进行存储，用 dpid 作为 key，因为需要记录开始时间，需要同样的字典来记录开始的时间戳，收到响应的时候，两个时间差存到对应的 dpid 中

    线路时延，同样用字典进行存储，用 (src_dpid, dst_dpid) 元组作为 key，寻找最短时延路径的时候，不用纠结 src host 到第一个 Switch 的时延和 最后一个 Switch 到 dst host 的时延，因为不管走哪条线路，最后这一部分肯定都是一样的

    记录所有的 datapath，同样是字典，用 dpid 作为key，到时候找到最短路径后，需要对路径上的 Switch 下发流表的时候需要相应的 datapath

    记录 host 和 Switch 相连接的端口，因为沉默的主机现象，主机不发送报文，Controller 是无法发现它的，应该调用 get_host 也可以，但我选择的是，Packet_In 的时候，检测这个端口是不是 Switch 和 Switch 之间的线路、Switch 和 Controller之间的线路，如果不是，说明连接的就是主机，用双层字典存储，dpid 作为外面的 key， host_ip 作为里面的 key，其实这次实验还是二层交换机，用 host_mac效果应该也一样

    因为打印路径要输出 Switch 的端口，因此 Switch 和 Switch 之间连接端口也要进行存储，用二层字典存储，cur_dpid 作为外面的 key， connect_dpid 作为里面的 key，值就是从 cur_dpid 出去的端口

    定义一个 switch ，用来存储通过 lookup_service_brick 获取到正在运行的 switches 实例，也就是我们之前修改源代码的那个类

* `sudo mn --custom topo_1970.py --topo generated --controller remote` ，这样执行好像不太对，拓扑文件中指定的时延好像没起作用，但是直接 python 或者直接 python3 执行会报错，我还是直接用老师给的虚拟机执行吧

    不行，那个虚拟机不够美观，我于是重新开了台虚拟机，重新配了下环境

* 寻找最短路径，用 networkx 提供的 API 就行，真的很好用！

    然后向路径上的 Switch下发流表，注意下发双向流表，去的线路和回来的路线都规划好，第一个和最后一个 Switch得特殊处理，因为连接到了 host，端口比较特殊



### 执行过程

#### mininet部分

直接 pingall

![image-20220413134440189](https://cdn.justinbob.site/typora/202204131344221.png)



当然，为了验证延迟是否正确，SDC ping MIT 也是有必要的

第一次时间会久一点，因为涉及到找最短路和下发流表

![image-20220414123534108](https://cdn.justinbob.site/typora/202204141235032.png)



#### ryu部分

注意执行的时候加上 `--observe-links`

查看 ryu 输出，因为我在下发流表的时候，将双向的路径都规划好了

可以看到，输出很有规律 10.0.0.1 有 8 条记录，逐渐递减，到最后 10.0.0.9 有 0 条记录，mininet输出的时候也是前面快后面慢，因为前面的路径都已经规划好了

可以看到第 17 行 MIT ping SDC 的路径，完全没问题

![image-20220413134335798](https://cdn.justinbob.site/typora/202204131343853.png)



#### 流表部分

S6 pingall之前的流表

![image-20220413134916969](https://cdn.justinbob.site/typora/202204131349036.png)



S6 pingall之后的流表

![image-20220413135020046](https://cdn.justinbob.site/typora/202204131350082.png)

![image-20220413135057600](https://cdn.justinbob.site/typora/202204131350635.png)

![image-20220413135118980](https://cdn.justinbob.site/typora/202204131351014.png)



## Part 2

### 实验思路

对 Part1 进行增强，打点补丁

* 首先是增加对端口状态变化的处理函数，原因都是 MODIFY，判断到底是端口断开 DOWN 还是连接上 LIVE
* 如果是链路断开，需要在拓扑图中删除这条 edge，当前已知最短路径中如果包含这条链路，需要将这条链路上所有 Switch 与源 IP、目标IP有关的流表项删除
* 如果是链路重新连接，重新计算所有已知的最短路径，如果计算出的结果与旧的记录不一致，表明路径应该更新，需要将旧的路径上 Switch 与源IP、目标IP有关的流表项删除
* 链路状态发生改变的时候，阻止广播风暴的记录也要清空，否则也会出现问题，本质上最后好像就是一颗生成树，如果某条重要链路断开，很可能导致 host 之间无法通信
    ARP 响应时候下发的流表项也要清除，否则后面的 ARP 响应顺着流表发送，很可能无法发送到目标 host



### 执行过程

#### mininet部分

首先我们执行 pingall

然后 link s8 s9 down，再次 pingall

最后 link s8 s9 up，再次 pingall

一个包都没丢，并且还都是最短路径，Great Success！

![image-20220413135939141](https://cdn.justinbob.site/typora/202204131359207.png)



最开始， SDC ping MIT

![image-20220414123804965](https://cdn.justinbob.site/typora/202204141238017.png)



link s8 s9 down，之后 SDC ping MIT

这里就比较奇怪了，看上面那张图，icmp_seq 1 和 icmp_seq 2 rtt 的时间差是 180ms

再看下面这张图，就隔了 10 ms，很奇怪，不是吗？都是重新规划路径，下发流表，唯一合理的解释，就是 ARP Cache，第一次双方需要进行 ARP Request 获得对方 mac 地址，后面缓存还没到期，就可以直接使用

但是我还是没想通，为什么老师给的那张图第一次只有 60 ms！

![image-20220414123857983](https://cdn.justinbob.site/typora/202204141238024.png)



link s8 s9 up，之后 SDC ping MIT

延迟波动比较大，我怀疑是我这台虚拟机性能不太行的原因

![image-20220414124017963](https://cdn.justinbob.site/typora/202204141240006.png)



#### ryu部分

注意执行的时候加上 `--observe-links`

第一次 pingall 执行完毕，所有的最短路径，跟 Part1 一致

第17行是 MIT ping SDC 的路径

![image-20220413140105841](https://cdn.justinbob.site/typora/202204131401894.png)



然后 link s8 s9 down

可以看到下面的这些路径发生了更新

第 40 行是 MIT ping SDC 的路径，发生了改变

![image-20220413140136955](https://cdn.justinbob.site/typora/202204131401020.png)



然后 link s8 s9 up

可以看到下面的这些路径发生了更新

第 47 行是 MIT ping SDC 的路径，已经恢复

![image-20220413140200362](https://cdn.justinbob.site/typora/202204131402401.png)



#### 流表部分

S6 pingall 之前的流表

![image-20220413140243662](https://cdn.justinbob.site/typora/202204131402699.png)



S6 第一次pingall 之后的流表

![image-20220413140325294](https://cdn.justinbob.site/typora/202204131403330.png)

![image-20220413140342211](https://cdn.justinbob.site/typora/202204131403269.png)

![image-20220413140403896](https://cdn.justinbob.site/typora/202204131404959.png)



S6 第二次 pingall 之后的流表

![image-20220413140434662](https://cdn.justinbob.site/typora/202204131404713.png)

![image-20220413140508356](https://cdn.justinbob.site/typora/202204131405439.png)

![image-20220413140539881](https://cdn.justinbob.site/typora/202204131405960.png)



S6 第三次 pingall 之后的流表

![image-20220413140634209](https://cdn.justinbob.site/typora/202204131406248.png)

![image-20220413140654886](https://cdn.justinbob.site/typora/202204131406957.png)

![image-20220413140721749](https://cdn.justinbob.site/typora/202204131407827.png)





link s8 s9 down，之后 S6 的流表长这样

从 39 条减少到了 28 条，只保留了那些没受影响的路径

![image-20220413141604251](https://cdn.justinbob.site/typora/202204131416329.png)

![image-20220413141622112](https://cdn.justinbob.site/typora/202204131416184.png)



## Addition

### Paper 中的办法

使用的是 OFDP V.2，能减少因为 LLDP 产生的 Packet_Out 的数量，但是 Packet_In 数量和 传统 OFDP 一样

具体流程如下，简单来说就是 Controller 向 Switch 发送一个 LLDP Packt_Out，Switch 收到后，修改为各个 active 端口的 src_mac，向各个端口转发

![image-20220412232057411](https://cdn.justinbob.site/typora/202204122320445.png)



### 我的想法

Controller 下发指令，开启 Switch 的 global lldp，Switch 自主学习，将学习到的信息存到本地的 MIB 数据库中

定时更新，Controller 每隔一段时间来取一次数据，得到拓扑信息，这个可以通过 SNMP 协议实现

触发式更新，如果 Switch 检测到拓扑发生变化，那么主动向 Controller 发送通告



因为传统的交换机本身就具备 LLDP 学习的能力，所以这部分其实不用 Controller 来管理

能显著减少因为 LLDP 产生的 Packet_In 和 Packet_Out 消息数量，提高带宽利用率，减轻 Controller 负担，有一点去中心化的意思

更重要的是能兼容那些不支持 OpenFlow 的传统交换机

缺点就是可能对拓扑结构的变化没有传统 OFDP 那么敏感，可能需要设计新的协议栈



发现一个巨大的问题，好像有的 OpenFlow Switch 不支持 LLDP，但是我的这种方法是必须要求 Switch 支持 LLDP 的

跟 `Efficient Topology Discovery for Software-Defined Networks` 这篇 Paper 想法差不多，可以在 IEEE 上找到

下面两张图是他们定义的 Switch 的状态机

<img src="https://cdn.justinbob.site/typora/202204142220821.png" style="width: 400px; margin-left: 0px; display: block; " />

<img src="https://cdn.justinbob.site/typora/202204142221633.png" style="width: 400px; margin-left: 0px; display: block; " />



## Source Code

如果觉得代码输出比较冗余的话，我这主要是为了调试方便，可以通过 Linux 的输出重定向输出到一个 txt 文件，然后再用 grep 提取出想要的内容



有一点进行增强，就是第一个 ipv4 Packet_In 的时候， Controller 规划好了路径，下发了流表

如果 msg 携带数据的话，第一个数据包可以通过控制器，直接发送给最后一个 Switch，能减小 80 ms 的延迟，前面是 290 ms，现在是 210 ms，链路本身大概 80 ms的延迟，那规划路径、下发流表、双方互相获得 mac 地址用了大概 120 ms

![image-20220416111540362](https://cdn.justinbob.site/typora/202204161115201.png)

代码改进如下

```python
data = None
        if msg.buffer_id == ofp.OFP_NO_BUFFER:

            # send packet out to the final switch
            data = msg.data
            out_port = port_final
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(datapath=self.datapath[dpid_final], buffer_id=ofp.OFP_NO_BUFFER,
                                      in_port=ofp.OFPP_CONTROLLER, actions=actions, data=data)
            self.datapath[dpid_final].send_msg(out)
            print('First ipv4 packet directly send to switch {}, it will forward the packet to port {}'
                  .format(dpid_final, port_final))

        else:

            # send packet out to the first switch
            out_port = self.switch_switch[short_path[0]][short_path[1]]
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(datapath=msg.datapath, buffer_id=msg.buffer_id,
                                      in_port=msg.match['in_port'], actions=actions, data=data)
            msg.datapath.send_msg(out)
```



### topo

```python
#!/usr/bin/python

"""
Custom topology for Mininet, generated by GraphML-Topo-to-Mininet-Network-Generator.
"""
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.node import Node
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.util import dumpNodeConnections

class GeneratedTopo( Topo ):
    "Internet Topology Zoo Specimen."

    def __init__( self, **opts ):
        "Create a topology."

        # Initialize Topology
        Topo.__init__( self, **opts )

        # add nodes, switches first...
        s1 = self.addSwitch( 's1' )
        s2 = self.addSwitch( 's2' )
        s3 = self.addSwitch( 's3' )
        s4 = self.addSwitch( 's4' )
        s5 = self.addSwitch( 's5' )
        s6 = self.addSwitch( 's6' )
        s7 = self.addSwitch( 's7' )
        s8 = self.addSwitch( 's8' )
        s9 = self.addSwitch( 's9' )

        # ... and now hosts
        h1 = self.addHost( 'HARVARD' )
        h2 = self.addHost( 'SRI' )
        h3 = self.addHost( 'UCSB' )
        h4 = self.addHost( 'UCLA' )
        h5 = self.addHost( 'RAND' )
        h6 = self.addHost( 'SDC' )
        h7 = self.addHost( 'UTAH' )
        h8 = self.addHost( 'MIT' )
        h9 = self.addHost( 'BBN' )

        # add edges between switch and corresponding host
        self.addLink( s1 , h1 )
        self.addLink( s2 , h2 )
        self.addLink( s3 , h3 )
        self.addLink( s4 , h4 )
        self.addLink( s5 , h5 )
        self.addLink( s6 , h6 )
        self.addLink( s7 , h7 )
        self.addLink( s8 , h8 )
        self.addLink( s9 , h9 )


        # add edges between switches
        self.addLink( s1 , s9, bw=10, delay='10ms')
        self.addLink( s2 , s3, bw=10, delay='11ms')
        self.addLink( s2 , s4, bw=10, delay='13ms')
        self.addLink( s3 , s4, bw=10, delay='14ms')
        self.addLink( s4 , s5, bw=10, delay='15ms')
        self.addLink( s5 , s9, bw=10, delay='29ms')
        self.addLink( s5 , s6, bw=10, delay='17ms')
        self.addLink( s6 , s7, bw=10, delay='10ms')
        self.addLink( s7 , s8, bw=10, delay='62ms')
        self.addLink( s8 , s9, bw=10, delay='17ms')


topos = { 'generated': ( lambda: GeneratedTopo() ) }

# HERE THE CODE DEFINITION OF THE TOPOLOGY ENDS

# the following code produces an executable script working with a remote controller
# and providing ssh access to the the mininet hosts from within the ubuntu vm
controller_ip = ''

def setupNetwork(controller_ip):
    "Create network and run simple performance test"
    # check if remote controller's ip was set
    # else set it to localhost
    topo = GeneratedTopo()
    if controller_ip == '':
        #controller_ip = '10.0.2.2';
        controller_ip = '127.0.0.1';
    net = Mininet(topo=topo, controller=lambda a: RemoteController( a, ip=controller_ip, port=6633 ), host=CPULimitedHost, link=TCLink)
    return net

def connectToRootNS( network, switch, ip, prefixLen, routes ):
    "Connect hosts to root namespace via switch. Starts network."
    "network: Mininet() network object"
    "switch: switch to connect to root namespace"
    "ip: IP address for root namespace node"
    "prefixLen: IP address prefix length (e.g. 8, 16, 24)"
    "routes: host networks to route to"
    # Create a node in root namespace and link to switch 0
    root = Node( 'root', inNamespace=False )
    intf = TCLink( root, switch ).intf1
    root.setIP( ip, prefixLen, intf )
    # Start network that now includes link to root namespace
    network.start()
    # Add routes from root ns to hosts
    for route in routes:
        root.cmd( 'route add -net ' + route + ' dev ' + str( intf ) )

def sshd( network, cmd='/usr/sbin/sshd', opts='-D' ):
    "Start a network, connect it to root ns, and run sshd on all hosts."
    switch = network.switches[ 0 ]  # switch to use
    ip = '10.123.123.1'  # our IP address on host network
    routes = [ '10.0.0.0/8' ]  # host networks to route to
    connectToRootNS( network, switch, ip, 8, routes )
    for host in network.hosts:
        host.cmd( cmd + ' ' + opts + '&' )

    dumpNodeConnections(network.hosts)


    CLI( network )
    for host in network.hosts:
        host.cmd( 'kill %' + cmd )
    network.stop()

# by zys
def start_network(network):
    network.start()

    dumpNodeConnections(network.hosts)


    CLI( network )
    network.stop()

if __name__ == '__main__':
    setLogLevel('info')
    #setLogLevel('debug')
    # sshd( setupNetwork(controller_ip) )
    start_network(setupNetwork(controller_ip))

```



### part1

```python
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

```



### part2

```python
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

        # store the shortest paths
        # shortest_paths[(start, end)] = [switch1, swith2, ..., switchn]
        self.shortest_paths = {}

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

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        dpid = dp.id
        ofp = dp.ofproto
        desc = msg.desc
        port_no = desc.port_no
        state = desc.state

        if msg.reason == ofp.OFPPR_ADD:
            reason = 'ADD'
        elif msg.reason == ofp.OFPPR_MODIFY:
            reason = 'MODIFY'

            if state == ofp.OFPPS_LINK_DOWN:
                print('a link is down!')

                # remove a link from the topo map
                next_switch = None
                for switch in self.switch_switch[dpid].keys():
                    if self.switch_switch[dpid][switch] == port_no:
                        next_switch = switch
                        break
                try:
                    self.topo_map.remove_edge(dpid, next_switch)
                except:
                    print('already remove the link')

                # delete flow entry
                # time.sleep(4)
                for ipv4, path in self.shortest_paths.items():
                    if dpid in path and next_switch in path:
                        self.delete_flow_entry(path, ipv4[0], ipv4[1])

            elif state == ofp.OFPPS_LIVE:
                print('a link is alive')

                # delete flow entry
                time.sleep(4)
                for ipv4_src, ipv4_dst in self.shortest_paths.keys():
                    dpid_begin = None
                    dpid_final = None

                    find_begin = False
                    for dpid in self.switch_host.keys():
                        for ip in self.switch_host[dpid].keys():
                            if ip == ipv4_src:
                                dpid_begin = dpid
                                find_begin = True
                                break
                        if find_begin:
                            break

                    find_final = False
                    for dpid in self.switch_host.keys():
                        for ip in self.switch_host[dpid].keys():
                            if ip == ipv4_dst:
                                dpid_final = dpid
                                find_final = True
                                break
                        if find_final:
                            break

                    new_path = nx.dijkstra_path(self.topo_map, dpid_begin, dpid_final)
                    old_path = self.shortest_paths[(ipv4_src, ipv4_dst)]
                    if new_path != old_path:
                        print('update a path')
                        self.delete_flow_entry(old_path, ipv4_src, ipv4_dst)

        elif msg.reason == ofp.OFPPR_DELETE:
            reason = 'DELETE'
        else:
            reason = 'unknown'

        print('OFPPortStatus received: reason={} port={}'.format(reason, port_no))

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
                # if src_dpid == 7 and dpid == 8:
                #     print('lldp delay between 7 and 8 is {}'.format(self.lldp_delay[(src_dpid, dpid)]))

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
            match = parser.OFPMatch(in_port=ofp.OFPP_ANY, eth_dst=eth_dst)
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
        self.shortest_paths[(ipv4_src, ipv4_dst)] = short_path

        min_delay = nx.dijkstra_path_length(self.topo_map, dpid_begin, dpid_final)
        print('nx find the shortest path {}, the min_delay is {}'.format(short_path, min_delay * 1000))

        if not short_path:
            return

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
            print(self.topo_map.edges)
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

    def delete_flow_entry(self, path, ipv4_src, ipv4_dst):

        print('Now remove table flow entry!')
        # avoid loop arp storm
        self.arp_in_port.clear()
        self.mac_to_port.clear()

        # delete all flow table entry
        for dpid in path:
            dp = self.datapath[dpid]
            parser = dp.ofproto_parser
            ofp = dp.ofproto

            match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_src, ipv4_dst=ipv4_dst)
            mod = parser.OFPFlowMod(datapath=dp, cookie=0, cookie_mask=0, table_id=0,
                                    command=ofp.OFPFC_DELETE, idle_timeout=0, hard_timeout=0,
                                    priority=1, buffer_id=ofp.OFPCML_NO_BUFFER,
                                    out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                    flags=0, match=match, instructions=None)
            dp.send_msg(mod)

            match = parser.OFPMatch(eth_type=0x800, ipv4_src=ipv4_dst, ipv4_dst=ipv4_src)
            mod = parser.OFPFlowMod(datapath=dp, cookie=0, cookie_mask=0, table_id=0,
                                    command=ofp.OFPFC_DELETE, idle_timeout=0, hard_timeout=0,
                                    priority=1, buffer_id=ofp.OFPCML_NO_BUFFER,
                                    out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                    flags=0, match=match, instructions=None)
            dp.send_msg(mod)
        for dp in self.datapath.values():
            parser = dp.ofproto_parser
            ofp = dp.ofproto

            match = parser.OFPMatch(in_port=ofp.OFPP_ANY)
            mod = parser.OFPFlowMod(datapath=dp, cookie=0, cookie_mask=0, table_id=0,
                                    command=ofp.OFPFC_DELETE, idle_timeout=0, hard_timeout=0,
                                    priority=10, buffer_id=ofp.OFPCML_NO_BUFFER,
                                    out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                    flags=0, match=match, instructions=None)
            dp.send_msg(mod)

```
