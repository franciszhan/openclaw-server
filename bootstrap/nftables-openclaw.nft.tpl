flush ruleset

define public_if = "__PUBLIC_IFACE__"
define bridge_if = "__BRIDGE_NAME__"
define bridge_net = __BRIDGE_NET__
define admin_nets = { __ADMIN_CIDRS__ }

table inet filter {
  chain input {
    type filter hook input priority 0;
    policy drop;

    ct state established,related accept
    iif "lo" accept
    ip protocol icmp accept
    iifname $public_if udp dport 41641 accept
    iifname "tailscale0" tcp dport 22 accept
    ip saddr $admin_nets tcp dport 22 accept
    iifname $bridge_if ip saddr $bridge_net tcp dport 22 accept
    iifname $bridge_if ip saddr $bridge_net accept
  }

  chain forward {
    type filter hook forward priority 0;
    policy drop;

    ct state established,related accept
    iifname $bridge_if oifname "lo" drop
    iifname $bridge_if oifname $bridge_if drop
    iifname $bridge_if oifname $public_if accept
    iifname $public_if oifname $bridge_if ct state established,related accept
  }

  chain output {
    type filter hook output priority 0;
    policy accept;
  }
}

table ip nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
__NAT_RULES__
  }
}
