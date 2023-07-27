#ifndef HEADER_fd_src_app_tguard_fd_tguard_cfg_h
#define HEADER_fd_src_app_tguard_fd_tguard_cfg_h


/* the pcap filter for capturing shreds generated by validators to be guarded    */
/*  filter syntax refers to: https://www.tcpdump.org/manpages/pcap-filter.7.html */
/*    replace the IP address with your validator IP address                      */
#define FD_TGUARD_LOCAL_SHRED_FILTER ("udp && src host 147.75.84.157")

/* the network interafce name for the turbine egress traffic */
/*     replace the network interface with one that carries  */
/*     above validator IP address as shown from `ifconfig`  */
#define FD_TGUARD_IFNAME ("bond0")

/* Max bandwidth in Mbps available for tguide operation */
/*   1Gbps supports up to 1e9/(1270*8)*4/2 = 196KTPS    */
#define FD_TGUARD_BW_MBPS (1000L)


/* log2 of max total data and code shreds to store*/
#define FD_TGUARD_SHREDSTORE_LG_ENTRY_CNT (20UL)

/* 2048B, sufficient to hold shred MTU */
#define FD_TGUARD_SHREDSTORE_LG_ENTRY_SIZ (11UL)

/* max 6 to fit bitmap into ulong. 
   Can set to values:
    - 2UL for max 128K data shreds per slot to support max 1.28MTPS,
    - 4UL for max  32K data shreds per slot to support max  320KTPS,
    - 6UL for max   8K data shreds per slot to support max   80KTPS */
#define FD_TGUARD_SHREDSTORE_LG_SLOT_CNT ( 6UL)


/*  8B Preamble & SoF + 4B CRC + 12B IPG */
#define FD_TGUARD_PKT_OVERHEAD (8L + 4L + 12L) 
/*  12B MACAddrs + 2B EthType + 20B IP Hdr + 8B UDP Hdr + UDPPayload */
#define FD_TGUARD_MAX_SHRED_PKT_SIZ (FD_TGUARD_PKT_OVERHEAD + 12L + 2L + 20L + 8L + 1228L) 


/* for each slot, wait lag (500ms, 1 slot + 100ms) 
   to collect shreds for said slot before sending */
#define FD_TGUARD_TX_LAG_US (500000L)

/* 32 is optimal for current Solana traffic ~5,000TPS */
#define FD_TGUARD_TX_STRIDE (32UL)

/* MUST be 1 for product operation, may set to 0 for testing */
#define FD_TGUARD_SHRED_DEDUP_ENA (1)

/* specify at compile time how logging to enable */
#define FD_TGUARD_DEBUGLVL (0)


#endif /* HEADER_fd_src_app_tguard_fd_tguard_cfg_h */
