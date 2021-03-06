# Copyright (c) 2014, Fundacion Dr. Manuel Sadosky
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.

# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
This modules implements the gadgets verifier. The given gadgets are
already classified, so for each one of them it generates a constraint
according to its type. Then the gadgets are translated to a logic
formula express in the SMTLIBv2 language. Finally, the formula and the
constrains are written down to a .smt file and a solver is call to check
validity.

This algorithm is architecture agnostic since it operates on the IR
representation of the underlying assembly code.
"""
from __future__ import absolute_import

from functools import reduce

import logging

import barf.core.smt.smtfunction as smtfunction

from barf.analysis.gadgets import GadgetType
from barf.core.reil import ReilRegisterOperand

logger = logging.getLogger("GadgetVerifier")


class GadgetVerifier(object):

    """Gadget Verifier.
    """

    def __init__(self, code_analyzer, architecture_info):

        # An instance of a Code Analyzer.
        self.analyzer = code_analyzer

        # Architecture information.
        self._arch_info = architecture_info

        # Constraints generators ordered by gadgets type.
        self._constraints_generators = {
            GadgetType.NoOperation:     self._get_constrs_no_operation,
            GadgetType.Jump:            self._get_constrs_jump,
            GadgetType.MoveRegister:    self._get_constrs_move_register,
            GadgetType.LoadConstant:    self._get_constrs_load_constant,
            GadgetType.Arithmetic:      self._get_constrs_arithmetic,
            GadgetType.LoadMemory:      self._get_constrs_load_memory,
            GadgetType.StoreMemory:     self._get_constrs_store_memory,
            GadgetType.ArithmeticLoad:  self._get_constrs_arithmetic_load,
            GadgetType.ArithmeticStore: self._get_constrs_arithmetic_store,
            GadgetType.Undefined:       None,
        }

        # Supported arithmetic and logical operations for arithmetic
        # gadgets.
        self._arithmetic_ops = {
            # Arithmetic
            "+": lambda x, y: x + y,
            "-": lambda x, y: x - y,

            # "*": lambda x, y: x * y,
            # "/": lambda x, y: x / y,
            # "%": lambda x, y: x % y,

            # Bitwise
            "&": lambda x, y: x & y,
            "^": lambda x, y: x ^ y,
            "|": lambda x, y: x | y,

            # "<<": lambda x, y: x << y,
            # ">>": lambda x, y: x >> y,
        }

    def verify(self, gadget):
        """Verify gadgets.
        """
        # Add instructions to the analyzer
        self.analyzer.reset()

        for reil_instr in gadget.ir_instrs:
            self.analyzer.add_instruction(reil_instr)

        # Generate constraints for the gadgets type.
        constrs = self._constraints_generators[gadget.type](gadget)

        # Check constraints.
        if not constrs:
            return False

        for constr in constrs:
            self.analyzer.add_constraint(constr)

        return self.analyzer.check() == 'unsat'

    # Verifiers
    # ======================================================================== #
    def _get_constrs_no_operation(self, gadget):
        """Verify NoOperation gadgets.
        """
        # Constraints on memory locations.
        # mem_constrs = [self.analyzer.get_memory("pre") != self.analyzer.get_memory("post")]
        mem_constrs = [self.analyzer.get_memory_curr("pre").__neq__(self.analyzer.get_memory_curr("post"))]

        # Constraints on flags.
        flags_constrs = []

        for name in self._arch_info.registers_flags:
            var_initial = self.analyzer.get_register_expr(name, mode="pre")
            var_final = self.analyzer.get_register_expr(name, mode="post")

            flags_constrs += [var_initial != var_final]

        # Constraints on registers.
        reg_constrs = []

        for name in self._arch_info.registers_gp_base:
            var_initial = self.analyzer.get_register_expr(name, mode="pre")
            var_final = self.analyzer.get_register_expr(name, mode="post")

            reg_constrs += [var_initial != var_final]

        # Make a big OR expression.
        constrs = mem_constrs + flags_constrs + reg_constrs
        constrs = [reduce(lambda c, acc: acc | c, constrs[1:], constrs[0])]

        return constrs

    def _get_constrs_jump(self, gadget):
        """Verify Jump gadgets.
        """
        return None

    def _get_constrs_move_register(self, gadget):
        """Generate constraints for the MoveRegister gadgets:
            dst <- src

        """
        # *src* register has to have the same value of *dst* for all
        # possibles assignments of *dst*.

        dst = self.analyzer.get_register_expr(gadget.destination[0].name, mode="post")
        src = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return [dst != src] + constrs_mod

    def _get_constrs_load_constant(self, gadget):
        """Generate constraints for the LoadConstant gadgets:
            dst <- constant

        """
        # *src* register has to have the same value of *dst* for all
        # possibles assignments of *dst*.

        dst = self.analyzer.get_register_expr(gadget.destination[0].name, mode="post")
        src = gadget.sources[0].immediate

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return [dst != src] + constrs_mod

    def _get_constrs_arithmetic(self, gadget):
        """Generate constraints for the BinaryOperation gadgets:
            dst <- src1 OP src2

        """
        # *dst* register has to have the value of *src1 op src2* for all
        # possibles assignments of *src1* and *src2*.

        dst = self.analyzer.get_register_expr(gadget.destination[0].name, mode="post")
        src1 = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")
        src2 = self.analyzer.get_register_expr(gadget.sources[1].name, mode="pre")
        op = self._arithmetic_ops[gadget.operation]

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return [dst != op(src1, src2)] + constrs_mod

    def _get_constrs_load_memory(self, gadget):
        """Generate constraints for the LoadMemory gadgets: dst_reg <- mem[src_reg + offset]
        """
        dst = self.analyzer.get_register_expr(gadget.destination[0].name, mode="post")
        size = gadget.destination[0].size

        if isinstance(gadget.sources[0], ReilRegisterOperand):
            base_addr = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")
            offset = gadget.sources[1].immediate

            addr = base_addr + offset
        else:
            addr = gadget.sources[1].immediate

        constrs = []

        for i in reversed(range(0, size, 8)):
            bytes_exprs_1 = self.analyzer.get_memory_expr(addr + i // 8, 8 // 8)
            bytes_exprs_2 = smtfunction.extract(dst, i, 8)

            constrs += [bytes_exprs_1 != bytes_exprs_2]

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return constrs + constrs_mod

    def _get_constrs_store_memory(self, gadget):
        """Generate constraints for the StoreMemory gadgets: mem[dst_reg + offset] <- src_reg
        """
        if isinstance(gadget.destination[0], ReilRegisterOperand):
            base_addr = self.analyzer.get_register_expr(gadget.destination[0].name, mode="pre")
            offset = gadget.destination[1].immediate

            addr = base_addr + offset
        else:
            addr = gadget.destination[1].immediate

        src = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")
        size = gadget.sources[0].size

        constrs = []

        for i in reversed(range(0, size, 8)):
            bytes_exprs_1 = self.analyzer.get_memory_expr(addr + i // 8, 8 // 8)
            bytes_exprs_2 = smtfunction.extract(src, i, 8)

            constrs += [bytes_exprs_1 != bytes_exprs_2]

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return constrs + constrs_mod

    def _get_constrs_arithmetic_load(self, gadget):
        """Generate constraints for the ArithmeticLoad gadgets: dst_reg <- dst_reg OP mem[src_reg + offset]
        """
        op = self._arithmetic_ops[gadget.operation]
        dst = self.analyzer.get_register_expr(gadget.destination[0].name, mode="post")
        size = gadget.destination[0].size

        if isinstance(gadget.sources[1], ReilRegisterOperand):
            base_addr = self.analyzer.get_register_expr(gadget.sources[1].name, mode="pre")
            offset = gadget.sources[2].immediate

            addr = base_addr + offset
        else:
            addr = gadget.sources[2].immediate

        src1 = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")
        src2 = self.analyzer.get_memory_expr(addr, size // 8)

        result = op(src1, src2)

        constrs = []

        for i in reversed(range(0, size, 8)):
            bytes_exprs_1 = smtfunction.extract(result, i, 8)
            bytes_exprs_2 = smtfunction.extract(dst, i, 8)

            constrs += [bytes_exprs_1 != bytes_exprs_2]

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return constrs + constrs_mod

    def _get_constrs_arithmetic_store(self, gadget):
        """Generate constraints for the ArithmeticStore gadgets: m[dst_reg + offset] <- m[dst_reg + offset] OP src_reg
        """
        if isinstance(gadget.sources[0], ReilRegisterOperand):
            base_addr = self.analyzer.get_register_expr(gadget.sources[0].name, mode="pre")
            offset = gadget.sources[1].immediate

            addr = base_addr + offset
        else:
            addr = gadget.sources[1].immediate

        op = self._arithmetic_ops[gadget.operation]
        size = gadget.sources[2].size
        src1 = self.analyzer.get_register_expr(gadget.sources[2].name, mode="pre")
        src2 = self.analyzer.get_memory_expr(addr, size // 8, mode="pre")
        dst = self.analyzer.get_memory_expr(addr, size // 8, mode="post")

        result = op(src1, src2)

        constrs = []

        for i in reversed(range(0, size, 8)):
            bytes_exprs_1 = smtfunction.extract(result, i, 8)
            bytes_exprs_2 = smtfunction.extract(dst, i, 8)

            constrs += [bytes_exprs_1 != bytes_exprs_2]

        # Check all non-modified registers don't change.
        constrs_mod = []

        for name in self._arch_info.registers_gp_base:
            if name not in [r.name for r in gadget.modified_registers]:
                var_initial = self.analyzer.get_register_expr(name, mode="pre")
                var_final = self.analyzer.get_register_expr(name, mode="post")

                constrs_mod += [var_initial != var_final]

        if constrs_mod:
            constrs_mod = [reduce(lambda c, acc: acc | c, constrs_mod[1:], constrs_mod[0])]

        return constrs + constrs_mod
