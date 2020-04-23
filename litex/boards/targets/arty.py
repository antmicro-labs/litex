#!/usr/bin/env python3

# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import argparse

from migen import *

from litex.boards.platforms import arty
from litex.build.xilinx.vivado import vivado_build_args, vivado_build_argdict

from litex.soc.cores.clock import *
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *
from litex.soc.cores.i2s import *
from litex.soc.cores import gpio

from litedram.modules import MT41K128M16
from litedram.phy import s7ddrphy

from liteeth.phy.mii import LiteEthPHYMII
from liteeth.mac import LiteEthMAC

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys       = ClockDomain()
        self.clock_domains.cd_sys4x     = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys4x_dqs = ClockDomain(reset_less=True)
        self.clock_domains.cd_clk200    = ClockDomain()
        self.clock_domains.cd_eth       = ClockDomain()
        self.clock_domains.cd_i2s       = ClockDomain()
        self.clock_domains.cd_i2s_lrck  = ClockDomain()

        # # #

        self.submodules.pll = pll = S7PLL(speedgrade=-1)

        cpu_reset = ~platform.request("cpu_reset")
        clk100 = platform.request("clk100")
        self.comb += pll.reset.eq(cpu_reset)
        pll.register_clkin(clk100, 100e6)

        pll.create_clkout(self.cd_sys,       sys_clk_freq)
        pll.create_clkout(self.cd_sys4x,     4*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x_dqs, 4*sys_clk_freq, phase=90)
        pll.create_clkout(self.cd_clk200,    200e6)
        pll.create_clkout(self.cd_eth,       25e6)
        pll.create_clkout(self.cd_i2s,       22.579e6)


        self.submodules.idelayctrl = S7IDELAYCTRL(self.cd_clk200)

        self.comb += platform.request("eth_ref_clk").eq(self.cd_eth.clk)
        self.comb += platform.request("i2s_rx_mclk").eq(self.cd_i2s.clk)
        self.comb += platform.request("i2s_tx_mclk").eq(self.cd_i2s.clk)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCSDRAM):
    def __init__(self, sys_clk_freq=int(100e6), integrated_rom_size=0x8000, **kwargs):
        platform = arty.Platform()

        # SoCSDRAM ---------------------------------------------------------------------------------
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                         integrated_rom_size=integrated_rom_size,
                         integrated_sram_size=0x8000,
                         **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # DDR3 SDRAM -------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = s7ddrphy.A7DDRPHY(platform.request("ddram"),
                memtype      = "DDR3",
                nphases      = 4,
                sys_clk_freq = sys_clk_freq)
            self.add_csr("ddrphy")
            sdram_module = MT41K128M16(sys_clk_freq, "1:4")
            self.register_sdram(self.ddrphy,
                geom_settings   = sdram_module.geom_settings,
                timing_settings = sdram_module.timing_settings)

# EthernetSoC --------------------------------------------------------------------------------------

class EthernetSoC(BaseSoC):
    mem_map = {
        "ethmac": 0xb0000000,
        "i2s_rx": 0xb1000000,
        "i2s_tx": 0xb2000000
    }
    mem_map.update(BaseSoC.mem_map)

    def __init__(self, **kwargs):
        BaseSoC.__init__(self, integrated_rom_size=0x10000, **kwargs)

        self.submodules.ethphy = LiteEthPHYMII(self.platform.request("eth_clocks"),
                                               self.platform.request("eth"))
        self.add_csr("ethphy")
        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32,
            interface="wishbone", endianness=self.cpu.endianness)
        self.add_wb_slave(self.mem_map["ethmac"], self.ethmac.bus, 0x2000)
        self.add_memory_region("ethmac", self.mem_map["ethmac"], 0x2000, type="io")
        self.add_csr("ethmac")
        self.add_interrupt("ethmac")
        # I2S --------------------------------------------------------------------------------------
        # i2s rx
        self.submodules.i2s_rx = S7I2SSlave(
            pads = self.platform.request("i2s_rx"),
        )
        self.add_memory_region("i2s_rx", self.mem_map["i2s_rx"],0x40000);
        self.add_wb_slave(self.mem_regions["i2s_rx"].origin, self.i2s_rx.bus,0x40000)
        self.add_csr("i2s_rx")
        self.add_interrupt("i2s_rx")
        # i2s tx
        i2s_tx = S7I2SSlave(
            pads = self.platform.request("i2s_tx"),
        )
        i2s_tx = ClockDomainsRenamer( {"write" : "sys", "read" : "pix"} )(i2s_tx)
        self.submodules.i2s_tx = i2s_tx 
        self.add_memory_region("i2s_tx", self.mem_map["i2s_tx"], 0x40000);
        self.add_wb_slave(self.mem_regions["i2s_tx"].origin, self.i2s_tx.bus,0x40000)
        self.add_csr("i2s_tx")
        self.add_interrupt("i2s_tx")

        self.platform.add_period_constraint(self.ethphy.crg.cd_eth_rx.clk, 1e9/12.5e6)
        self.platform.add_period_constraint(self.ethphy.crg.cd_eth_tx.clk, 1e9/12.5e6)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            self.ethphy.crg.cd_eth_rx.clk,
            self.ethphy.crg.cd_eth_tx.clk)
        # gpio
        port_led = []
        for i in range(4):
            port_led += [self.platform.request("user_led", i)]
        self.submodules.port_led = gpio.GPIOOut(port_led)
        self.add_csr("port_led")


# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Arty")
    builder_args(parser)
    soc_sdram_args(parser)
    vivado_build_args(parser)
    parser.add_argument("--with-ethernet", action="store_true",
                        help="enable Ethernet support")
    args = parser.parse_args()

    cls = EthernetSoC if args.with_ethernet else BaseSoC
    soc = cls(**soc_sdram_argdict(args))
    
    builder = Builder(soc, **builder_argdict(args))
    builder.build(**vivado_build_argdict(args))


if __name__ == "__main__":
    main()
