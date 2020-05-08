#!/usr/bin/env python3

# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import os
import argparse

from migen import *

from litex.boards.platforms import arty
from litex.build.xilinx.vivado import vivado_build_args, vivado_build_argdict

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *
from litex.soc.cores.i2s import *
from litex.soc.cores import gpio

from litedram.modules import MT41K128M16
from litedram.phy import s7ddrphy

from liteeth.phy.mii import LiteEthPHYMII

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys       = ClockDomain()
        self.clock_domains.cd_sys2x     = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys4x     = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys4x_dqs = ClockDomain(reset_less=True)
        self.clock_domains.cd_clk200    = ClockDomain()
        self.clock_domains.cd_eth       = ClockDomain()
        self.clock_domains.cd_i2s_tx    = ClockDomain()

        # # #

        self.submodules.pll = pll = S7PLL(speedgrade=-1)
        self.submodules.pll2 = pll2 = S7PLL(speedgrade=-1)

        cpu_reset = ~platform.request("cpu_reset")
        clk100 = platform.request("clk100")
        self.comb += pll.reset.eq(cpu_reset)
        pll.register_clkin(clk100, 100e6)
        self.comb += pll2.reset.eq(cpu_reset)
        pll2.register_clkin(clk100, 100e6)

        pll.create_clkout(self.cd_sys,       sys_clk_freq)
        pll.create_clkout(self.cd_sys2x,     2*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x,     4*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x_dqs, 4*sys_clk_freq, phase=90)
        pll.create_clkout(self.cd_clk200,    200e6)
        pll.create_clkout(self.cd_eth,       25e6)

        self.submodules.idelayctrl = S7IDELAYCTRL(self.cd_clk200)

        self.comb += platform.request("eth_ref_clk").eq(self.cd_eth.clk)

        rx_mclk = platform.request("i2s_rx_mclk")
        tx_mclk = platform.request("i2s_tx_mclk")
        mclk_freq_rx=8192 
        mclk_period_rx=int(sys_clk_freq/(mclk_freq_rx*2))
        mclk_counter = Signal(16)
        self.sync+= [
                If((mclk_counter == mclk_period_rx),
                        mclk_counter.eq(0),
                        rx_mclk.eq(~rx_mclk),
                        tx_mclk.eq(~tx_mclk),
                ).Else(
                   mclk_counter.eq(mclk_counter + 1)
                )
        ]

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(100e6), with_ethernet=False, with_etherbone=False, **kwargs):
        platform = arty.Platform()

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, clk_freq=sys_clk_freq, **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # DDR3 SDRAM -------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = s7ddrphy.A7DDRPHY(platform.request("ddram"),
                memtype        = "DDR3",
                nphases        = 4,
                sys_clk_freq   = sys_clk_freq,
                interface_type = "MEMORY")
            self.add_csr("ddrphy")
            self.add_sdram("sdram",
                phy                     = self.ddrphy,
                module                  = MT41K128M16(sys_clk_freq, "1:4"),
                origin                  = self.mem_map["main_ram"],
                size                    = kwargs.get("max_sdram_size", 0x40000000),
                l2_cache_size           = kwargs.get("l2_size", 8192),
                l2_cache_min_data_width = kwargs.get("min_l2_data_width", 128),
                l2_cache_reverse        = True
            )

        # Ethernet ---------------------------------------------------------------------------------
        if with_ethernet:
            self.submodules.ethphy = LiteEthPHYMII(
                clock_pads = self.platform.request("eth_clocks"),
                pads       = self.platform.request("eth"))
            self.add_csr("ethphy")
            self.add_ethernet(phy=self.ethphy)

        # Etherbone --------------------------------------------------------------------------------
        if with_etherbone:
            self.submodules.ethphy = LiteEthPHYMII(
                clock_pads = self.platform.request("eth_clocks"),
                pads       = self.platform.request("eth"))
            self.add_csr("ethphy")
            self.add_etherbone(phy=self.ethphy)

# SoundSoC --------------------------------------------------------------------------------------

class SoundSoC(BaseSoC):
    mem_map = {
        "i2s_rx": 0xb1000000,
        "i2s_tx": 0xb2000000
    }
    mem_map.update(BaseSoC.mem_map)
    def __init__(self, **kwargs):
        BaseSoC.__init__(self, **kwargs)
        # I2S --------------------------------------------------------------------------------------
        i2s_mem_size=0x40000;
        # i2s rx
        self.submodules.i2s_rx = S7I2SSlave(
            pads=self.platform.request("i2s_rx"),
            sample_width=16,
            frame_format=I2S_FORMAT.I2S_STANDARD,
            concatenate_channels=False,
            master=True,
            lrck_freq=16000,
            bits_per_channel=28
        )
        self.add_memory_region("i2s_rx", self.mem_map["i2s_rx"], i2s_mem_size);
        self.add_wb_slave(self.mem_regions["i2s_rx"].origin, self.i2s_rx.bus, i2s_mem_size)
        self.add_csr("i2s_rx")
        self.add_interrupt("i2s_rx")
        # i2s tx
        self.submodules.i2s_tx = S7I2SSlave(
            pads=self.platform.request("i2s_tx"),
            sample_width=16,
            frame_format=I2S_FORMAT.I2S_STANDARD,
            master=True,
            concatenate_channels=False,
            lrck_freq=16000,
            bits_per_channel=28
        )
        self.add_memory_region("i2s_tx", self.mem_map["i2s_tx"], i2s_mem_size);
        self.add_wb_slave(self.mem_regions["i2s_tx"].origin, self.i2s_tx.bus, i2s_mem_size)
        self.add_csr("i2s_tx")
        self.add_interrupt("i2s_tx")

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Arty A7")
    parser.add_argument("--build", action="store_true", help="Build bitstream")
    parser.add_argument("--load",  action="store_true", help="Load bitstream")
    builder_args(parser)
    soc_sdram_args(parser)
    vivado_build_args(parser)
    parser.add_argument("--with-ethernet",  action="store_true", help="Enable Ethernet support")
    parser.add_argument("--with-etherbone", action="store_true", help="Enable Etherbone support")
    args = parser.parse_args()

    assert not (args.with_ethernet and args.with_etherbone)
    soc =SoundSoC(with_ethernet=args.with_ethernet, with_etherbone=args.with_etherbone,
        **soc_sdram_argdict(args))
    builder = Builder(soc, **builder_argdict(args))
    builder.build(**vivado_build_argdict(args), run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, "top.bit"))

if __name__ == "__main__":
    main()
