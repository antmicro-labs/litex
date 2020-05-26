# This file is Copyright (c) 2013-2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# License: BSD

from migen import *
from migen.genlib.cdc import MultiReg

from litex.soc.interconnect.csr import *


class GPIOIn(Module, AutoCSR):
    def __init__(self, signal):
        sig_len = len(signal)
        self._in = CSRStatus(sig_len)
        for i in range(sig_len):
            self.specials += MultiReg(signal[i], self._in.status[i])


class GPIOOut(Module, AutoCSR):
    def __init__(self, signal):
        sig_len = len(signal)
        self._out = CSRStorage(sig_len)
        for i in range(sig_len):
            self.comb += signal[i].eq(self._out.storage[i])


class GPIOInOut(Module):
    def __init__(self, in_signal, out_signal):
        self.submodules.gpio_in = GPIOIn(in_signal)
        self.submodules.gpio_out = GPIOOut(out_signal)

    def get_csrs(self):
        return self.gpio_in.get_csrs() + self.gpio_out.get_csrs()


class Blinker(Module):
    def __init__(self, signal, divbits=26):
        counter = Signal(divbits)
        self.comb += signal.eq(counter[divbits-1])
        self.sync += counter.eq(counter + 1)
