#!/usr/bin/env python

# Copyright (c) 2017, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
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

import collections
import functools
import sys

import numpy
COUNTTYPE = numpy.float64

import histbook.expr
import histbook.stmt
import histbook.axis
import histbook.calc

class Fillable(object):
    @property
    def fields(self):
        if self._fields is None:
            table = {}
            goals = set(self._goals)

            for x in goals:
                x.clear()
            for x in goals:
                x.grow(table)

            fields = histbook.stmt.sources(goals, table)

            self._instructions = self._streamline(0, list(histbook.stmt.instructions(fields, goals)))
            self._fields = sorted(x.goal.value for x in fields)

        return self._fields

    def _fill(self, arrays):
        self.fields  # for the side-effect of creating self._instructions

        symbols = {}
        for instruction in self._instructions:
            if isinstance(instruction, histbook.stmt.Param):
                symbols[instruction.name] = arrays[instruction.extern]

            elif isinstance(instruction, histbook.stmt.Assign):
                symbols[instruction.name] = histbook.calc.calculate(instruction.expr, symbols)

            elif isinstance(instruction, histbook.stmt.Export):
                data = symbols[instruction.name]
                for i, j in instruction.destination:
                    self._destination[i][j] = data

            elif isinstance(instruction, histbook.stmt.Delete):
                del symbols[instruction.name]

            else:
                raise AssertionError(instruction)

class Book(collections.MutableMapping, Fillable):
    def __init__(self, hists={}, **keywords):
        self._fields = None
        self._hists = collections.OrderedDict()
        for n, x in hists.items():
            self._hists[n] = x
        for n, x in keywords.items():
            self._hists[n] = x

    def __repr__(self):
        return "Book({0} histogram{1})".format(len(self), "" if len(self) == 1 else "s")

    def __str__(self):
        return "Book({" + ",\n      ".join("{0}: {1}".format(repr(n), repr(x)) for n, x in self.items()) + "})"

    def __len__(self):
        return len(self._hists)

    def __getitem__(self, name):
        return self._hists[name]

    def __setitem__(self, name, value):
        if isinstance(value, Book):
            for n, x in value.items():
                self._hists[name + "/" + n] = x.copy()
                self._fields = None
        elif isinstance(value, Hist):
            self._hists[name] = value.copy()
            self._fields = None
        else:
            raise TypeError("histogram books can only be filled with histograms or other histogram books, not {0}".format(type(value)))

    def __delitem__(self, name):
        del self._hists[name]

    def __iter__(self):
        if sys.version_info[0] < 3:
            return self._hists.iterkeys()
        else:
            return self._hists.keys()

    @property
    def _goals(self):
        return functools.reduce(set.union, (x._goals for x in self.values()))

    def _streamline(self, i, instructions):
        self._destination = []
        for i, x in enumerate(self._hists.values()):
            self._destination.append(x._destination[0])
            x._streamline(i, instructions)
        return instructions

    def fill(self, **arrays):
        for x in self._hists.values():
            x._prefill()
        self._fill(arrays)
        for x in self._hists.values():
            x._postfill(arrays)
        
class Hist(Fillable):
    def weight(self, expr):
        return Hist(*[x.relabel(x._original) for x in self._group + self._fixed + self._profile], defs=self._defs, weight=expr)

    def copy(self):
        out = self.__class__.__new__(self.__class__)
        out.__dict__.update(self.__dict__)
        def copy(content):
            if content is None:
                return None
            elif isinstance(content, numpy.ndarray):
                return content.copy()
            else:
                return dict((n, copy(x)) for n, x in content.items())
        out._content = copy(self._content)
        return out

    def __init__(self, *axis, **opts):
        if len(axis) == 0:
            raise TypeError("Hist must have at least one axis")

        weight = opts.pop("weight", None)
        defs = opts.pop("defs", {})
        if len(opts) > 0:
            raise TypeError("unrecognized options for Hist: {0}".format(" ".join(opts)))

        self._defs = defs
        self._group = []
        self._fixed = []
        self._profile = []

        newaxis = []
        for old in axis:
            expr, label = histbook.expr.Expr.parse(old._expr, defs=defs, returnlabel=True)
            new = old.relabel(label)
            new._original = old._expr
            new._parsed = expr
            newaxis.append(new)

        self._goals = set()
        self._destination = [[]]
        self._lookup = {}
        def dest(goals):
            self._goals.update(set(goals))
            for goal in goals:
                if goal.goal not in self._lookup:
                    self._lookup[goal.goal] = []
                self._lookup[goal.goal].append(len(self._destination[0]))
                self._destination[0].append(None)

        dictindex = 0
        for new in newaxis:
            if isinstance(new, histbook.axis.GroupAxis):
                self._group.append(new)
                new._dictindex = dictindex
                dictindex += 1
                dest(new._goals(new._parsed))

        self._shape = []
        for new in newaxis:
            if isinstance(new, histbook.axis.FixedAxis):
                self._fixed.append(new)
                new._shapeindex = len(self._shape)
                self._shape.append(new.totbins)
                dest(new._goals(new._parsed))

        self._shape.append(0)
        for new in newaxis:
            if isinstance(new, histbook.axis.ProfileAxis):
                self._profile.append(new)
                new._sumwxindex = self._shape[-1]
                new._sumwx2index = self._shape[-1] + 1
                self._shape[-1] += 2
                dest(new._goals(new._parsed))

        if weight is None:
            self._weightoriginal, self._weightparsed, self._weightlabel = None, None, None
            self._sumwindex = self._shape[-1]
            self._shape[-1] += 1

        else:
            self._weightoriginal = weight
            self._weightparsed, self._weightlabel = histbook.expr.Expr.parse(weight, defs=self._defs, returnlabel=True)
            self._sumwindex = self._shape[-1]
            self._sumw2index = self._shape[-1] + 1
            self._shape[-1] += 2
            dest([histbook.stmt.CallGraphGoal(self._weightparsed),
                  histbook.stmt.CallGraphGoal(histbook.expr.Call("numpy.multiply", self._weightparsed, self._weightparsed))])

        self._weight = weight
        self._shape = tuple(self._shape)
        self._content = None
        self._fields = None
        
    def __repr__(self, indent=", "):
        out = [repr(x) for x in self._group + self._fixed + self._profile]
        if self._weightlabel is not None:
            out.append("weight={0}".format(repr(self._weightlabel)))
        if len(self._defs) > 0:
            out.append("defs={" + ", ".join("{0}: {1}".format(repr(n), repr(str(x)) if isinstance(x, histbook.expr.Expr) else repr(x)) for n, x in self._defs.items()) + "}")
        return "Hist(" + indent.join(out) + ")"

    def __str__(self):
        return self.__repr__(",\n     ")

    @property
    def shape(self):
        return self._shape

    def _streamline(self, i, instructions):
        for instruction in instructions:
            if isinstance(instruction, histbook.stmt.Export):
                if not hasattr(instruction, "destination"):
                    instruction.destination = []
                if instruction.goal in self._lookup:
                    for j in self._lookup[instruction.goal]:
                        instruction.destination.append((i, j))

        return instructions

    def fill(self, **arrays):
        self._prefill()
        self._fill(arrays)
        self._postfill(arrays)

    def _prefill(self):
        if self._content is None:
            if len(self._group) == 0:
                self._content = numpy.zeros(self._shape, dtype=COUNTTYPE)
            else:
                self._content = {}

    def _postfill(self, arrays):
        j = len(self._group)
        step = 0
        for axis in self._fixed:
            if step == 0:
                indexes = self._destination[0][j]
            elif step == 1:
                indexes = indexes.copy()
            if step > 0:
                numpy.multiply(indexes, self._shape[axis._shapeindex], indexes)
                numpy.add(indexes, self._destination[0][j], indexes)
            j += 1
            step += 1

        axissumx, axissumx2 = [], []
        for axis in self._profile:
            axissumx.append(self._destination[0][j])
            axissumx2.append(self._destination[0][j + 1])
            j += 2

        if self._weightparsed is None:
            weight = 1
            weight2 = None
        else:
            weight = self._destination[0][j]
            weight2 = self._destination[0][j + 1]
            selection = numpy.isnan(weight)
            if selection.any():
                weight = weight.copy()
                weight2 = weight2.copy()
                weight[selection] = 0.0
                weight2[selection] = 0.0

        def fillblock(content, indexes, axissumx, axissumx2, weight, weight2):
            for sumx, sumx2, axis in zip(axissumx, axissumx2, self._profile):
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, axis._sumwxindex], indexes.compressed(), sumx * weight)
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, axis._sumwx2index], indexes.compressed(), sumx2 * weight)

            if weight2 is None:
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumwindex], indexes.compressed(), weight)
            else:
                selection = numpy.ma.getmask(indexes)
                if selection is not numpy.ma.nomask:
                    selection = numpy.bitwise_not(selection)
                    weight = weight[selection]
                    weight2 = weight2[selection]
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumwindex], indexes.compressed(), weight)
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumw2index], indexes.compressed(), weight2)

        def filldict(j, content, indexes, axissumx, axissumx2, weight, weight2):
            if j == len(self._group):
                fillblock(content, indexes, axissumx, axissumx2, weight, weight2)

            else:
                uniques, inverse = self._destination[0][j]
                for idx, unique in enumerate(uniques):
                    selection = (inverse == idx)
                    antiselection = numpy.bitwise_not(selection)

                    if unique not in content:
                        if j + 1 == len(self._group):
                            content[unique] = numpy.zeros(self._shape, dtype=COUNTTYPE)
                        else:
                            content[unique] = {}

                    subcontent = content[unique]
                    numpy.bitwise_or(antiselection, numpy.ma.getmaskarray(indexes), antiselection)
                    subindexes = numpy.ma.array(data=numpy.ma.getdata(indexes), mask=antiselection)
                    subaxissumx = [x[selection] for x in axissumx]
                    subaxissumx2 = [x[selection] for x in axissumx2]
                    if weight2 is None:
                        subweight, subweight2 = weight, weight2
                    else:
                        subweight = weight[selection]
                        subweight2 = weight2[selection]

                    filldict(j + 1, subcontent, subindexes, subaxissumx, subaxissumx2, subweight, subweight2)

        filldict(0, self._content, indexes, axissumx, axissumx2, weight, weight2)
            
        for j in range(len(self._destination[0])):
            self._destination[0][j] = None