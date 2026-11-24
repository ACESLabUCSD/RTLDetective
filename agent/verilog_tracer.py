import os
import re
import tempfile

from pyverilog.vparser.parser import parse
from pyverilog.vparser.ast import *
import matplotlib.pyplot as plt
import networkx as nx

def format_path(path):
    return "<=".join(path)


def _blank_comments(text):
    """Replace comments with spaces (newlines kept), so positions and line
    numbers stay the same. String literals are left alone."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"' and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [c if c == "\n" else " " for c in text[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


def split_comma_assigns(text):
    """`assign a = x, b = y;` -> `assign a = x; assign b = y;`"""
    clean = _blank_comments(text)
    out, pos, changed = [], 0, False
    for m in re.finditer(r"\bassign\b", clean):
        if m.start() < pos:
            continue
        depth, j = 0, m.end()
        while j < len(clean) and not (clean[j] == ";" and depth == 0):
            c = clean[j]
            if c == '"':   # skip string literals
                j += 1
                while j < len(clean) and clean[j] not in '"\n':
                    j += 2 if clean[j] == "\\" else 1
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 0:
                # put the new `assign` where the next assignment starts, so it
                # keeps its own line number
                k = j + 1
                while k < len(clean) and clean[k].isspace():
                    k += 1
                out.append(clean[pos:j] + ";" + clean[j + 1:k] + "assign ")
                pos, changed = k, True
                j = k - 1
            j += 1
        out.append(clean[pos:j])
        pos = j
    out.append(clean[pos:])
    return "".join(out) if changed else text

class VerilogTracer:
    """Signal dependency graph of a design, elaborated from its top module.

    Top-module signals keep their bare names; signals inside a
    submodule instance are named by hierarchical path, the same names the VCD dump uses below the DUT.
    """

    def __init__(self, file_path, top=None):
        self.file_path = file_path
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.source_lines = source.splitlines(keepends=True)
        self.ast = self._parse(file_path, source)
        self.graph = nx.DiGraph()
        self.assign_map = {}
        self.build_graph(top)

    def build_graph(self, top=None):
        self.modules = {}
        self._collect_modules(self.ast)
        self.top = top if top in self.modules else self._pick_top()
        self.params = set()
        if self.top is not None:
            self.top_ports = self._module_ports(self.modules[self.top])
            self._elaborate(self.modules[self.top], "", depth=0)
        else:
            self.top_ports = []
            self._visit(self.ast, "", 0)
        self.filter_graph()

    def filter_graph(self):
        """
        Remove parameters and localparams from the graph, as they are not relevant for signal tracing
        """
        for n in list(self.graph.nodes()):
            if n in self.params:
                self.graph.remove_node(n)

    def get_output_ports(self):
        return [n for n, d in self.top_ports if d == "output" and n in self.graph]

    def output_cone(self) -> set[str]:
        """Every signal that a top-module output or inout depends on, plus those
        ports (names as in the graph = VCD paths relative to the DUT)."""
        ports = {n for n, d in self.top_ports if d in ("output", "inout")}
        cone = set(ports)
        for p in ports:
            if p in self.graph:
                cone |= nx.ancestors(self.graph, p)
        return cone

    # ---------------------------------------------------------------- parsing

    @staticmethod
    def _parse(file_path, source):
        """Parse with pyverilog. If the file has statements pyverilog cannot read parse a rewritten copy instead;"""
        fixed = split_comma_assigns(source)
        if fixed == source:
            return parse([file_path])[0]
        fd, tmp = tempfile.mkstemp(suffix=".v", prefix="vtrace_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(fixed)
            return parse([tmp], preprocess_include=[os.path.dirname(os.path.abspath(file_path))])[0]
        finally:
            os.remove(tmp)

    # ---------------------------------------------------------------- modules

    def _collect_modules(self, node):
        if isinstance(node, ModuleDef):
            self.modules[node.name] = node
            return
        for c in node.children():
            self._collect_modules(c)

    def _instances(self, node):
        if isinstance(node, Instance):
            yield node
        for c in node.children():
            yield from self._instances(c)

    def _pick_top(self):
        """The module no other module instantiates (preferring one that has
        instances itself, then the last one defined)."""
        if not self.modules:
            return None
        used = {i.module for m in self.modules.values() for i in self._instances(m)}
        names = list(self.modules)
        roots = [n for n in names if n not in used] or names
        with_children = [n for n in roots if any(True for _ in self._instances(self.modules[n]))]
        return (with_children or roots)[-1]

    def _module_ports(self, mdef):
        """[(port name, "input"|"output"|"inout"|None)] in declaration order."""
        dirs = {}

        def visit(n):
            if isinstance(n, (Input, Output, Inout)):
                dirs.setdefault(n.name, type(n).__name__.lower())
            for c in n.children():
                visit(c)

        visit(mdef)
        ports = []
        for p in (mdef.portlist.ports if mdef.portlist else []):
            name = p.first.name if isinstance(p, Ioport) else getattr(p, "name", None)
            if name is not None:
                ports.append((name, dirs.get(name)))
        return ports

    def _elaborate(self, mdef, prefix, depth):
        self._visit(mdef, prefix, depth)

    def _connect_instance(self, inst, prefix, depth):
        child = self.modules.get(inst.module)
        if child is None or depth > 32:   # unknown (library) module, or runaway recursion
            return
        cprefix = f"{prefix}{inst.name}."
        ports = self._module_ports(child)
        dirs = dict(ports)
        for i, pa in enumerate(inst.portlist):
            pname = pa.portname if pa.portname is not None else (ports[i][0] if i < len(ports) else None)
            if pname is None or pa.argname is None:
                continue
            cport = cprefix + pname
            self._register_node(cport)
            if dirs.get(pname) in ("input", "inout"):
                for s in self._extract_rhs(pa.argname):
                    self._add_edge(prefix + s, cport, "comb")
            if dirs.get(pname) in ("output", "inout"):
                for n in self._lhs(pa.argname)[0]:
                    self._add_edge(cport, prefix + n, "comb")
                    self.assign_map.setdefault(prefix + n, []).append((inst.lineno, inst))
        self._elaborate(child, cprefix, depth + 1)

    # ---------------------------------------------------------- statements

    def _register_node(self, sig):
        if sig is not None and sig not in self.graph:
            self.graph.add_node(sig)

    def _add_edge(self, src, dst, edge_type):
        self._register_node(src)
        self._register_node(dst)
        self.graph.add_edge(src, dst, node_type=edge_type)

    def _add_assign(self, left, right, prefix, edge_type, lineno, node, cond_deps=()):
        """lhs <- every rhs signal, every index used on the lhs, and the
        enclosing if/case conditions."""
        lhs, index_deps = self._lhs(left)
        deps = self._extract_rhs(right) | index_deps | set(cond_deps)
        for l in lhs:
            ql = prefix + l
            self._register_node(ql)
            for s in deps:
                self._add_edge(prefix + s, ql, edge_type)
            self.assign_map.setdefault(ql, []).append((lineno, node))

    def _visit(self, node, prefix, depth):
        if isinstance(node, (Parameter, Localparam)):
            self.params.add(prefix + node.name)
        elif isinstance(node, Assign):
            self._add_assign(node.left, node.right, prefix, "comb", node.lineno, node)
        elif isinstance(node, Always):
            self._handle_always(node, prefix)
            return
        elif isinstance(node, Instance):
            self._connect_instance(node, prefix, depth)
            return
        for c in node.children():
            self._visit(c, prefix, depth)

    def _handle_always(self, node, prefix):
        is_seq = False
        if isinstance(node.sens_list, SensList):
            for sens in node.sens_list.list:
                if sens.type in ('posedge', 'negedge'):
                    is_seq = True
        edge_type = "seq" if is_seq else "comb"

        def process(stmt, cond_deps=None):
            if cond_deps is None:
                cond_deps = set()

            if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
                self._add_assign(stmt.left, stmt.right, prefix, edge_type, stmt.lineno, stmt, cond_deps)

            elif isinstance(stmt, IfStatement):
                cond = self._extract_rhs(stmt.cond)
                for s in cond:
                    self._register_node(prefix + s)
                new_cond = cond_deps | cond
                if stmt.true_statement:
                    process(stmt.true_statement, new_cond)
                if stmt.false_statement:
                    process(stmt.false_statement, new_cond)
                return

            elif isinstance(stmt, CaseStatement):
                cond = self._extract_rhs(stmt.comp)
                for s in cond:
                    self._register_node(prefix + s)
                new_cond = cond_deps | cond
                for case in stmt.caselist:
                    if case.statement:
                        process(case.statement, new_cond)
                return

            for c in stmt.children():
                process(c, cond_deps)

        process(node.statement)

    def _extract_rhs(self, node):
        signals = set()
        if isinstance(node, Identifier):
            signals.add(node.name)
        elif isinstance(node, (Pointer, Partselect)):
            signals |= self._extract_rhs(node.var)
        for c in node.children():
            signals |= self._extract_rhs(c)
        return signals

    def _lhs(self, node):
        """(assigned signal names, signals used in lhs indexes), e.g.
        RAM_MEM[waddr] -> (["RAM_MEM"], {"waddr"}); {a, b[i]} -> (["a", "b"], {"i"})."""
        if isinstance(node, Identifier):
            return [node.name], set()
        if isinstance(node, Lvalue):
            return self._lhs(node.var)
        if isinstance(node, Pointer):
            names, idx = self._lhs(node.var)
            return names, idx | self._extract_rhs(node.ptr)
        if isinstance(node, Partselect):
            names, idx = self._lhs(node.var)
            return names, idx | self._extract_rhs(node.msb) | self._extract_rhs(node.lsb)
        if isinstance(node, Concat):   # also LConcat
            names, idx = [], set()
            for item in node.list:
                n, i = self._lhs(item)
                names += n
                idx |= i
            return names, idx
        return [], set()

    def _get_name(self, node):
        names = self._lhs(node)[0]
        return names[0] if names else None

    def trace_signals(self, targets):
        visited = set()
        stack = list(targets)
        out = []
        while stack:
            s = stack.pop()
            if s in visited:
                continue
            visited.add(s)
            if s in self.assign_map:
                for ln, _ in self.assign_map[s]:
                    out.append((s, ln))
            if s not in self.graph:
                continue
            for p in self.graph.predecessors(s):
                stack.append(p)
        return out
    
    def export_llm_dependencies(self, target_signal):
        """
        Export dependency chains with edge direction and type (seq/comb)
        """

        dependencies = []
        visited_edges = set()
        stack = [target_signal]
        visited_nodes = set()

        while stack:
            node = stack.pop()
            if node in visited_nodes:
                continue
            visited_nodes.add(node)
            if node not in self.graph:
                continue
            for pred in self.graph.predecessors(node):
                edge_data = self.graph.get_edge_data(pred, node)
                edge_type = edge_data.get("node_type", "unknown")

                edge_repr = f"{node} <- {pred} ({edge_type})"

                if edge_repr not in visited_edges:
                    dependencies.append(edge_repr)
                    visited_edges.add(edge_repr)

                stack.append(pred)

        return {
            "target": target_signal,
            "dependencies": dependencies
        }

    def visualize_graph(self, save_path="graph.png", highlight=None):
        if highlight is None:
            highlight = []
        plt.figure(figsize=(12, 10))
        pos = nx.spring_layout(self.graph, seed=42)
        colors = ["red" if n in highlight else "skyblue" for n in self.graph.nodes()]
        nx.draw_networkx_nodes(self.graph, pos, node_color=colors, node_size=800)
        nx.draw_networkx_edges(self.graph, pos, arrows=True, arrowstyle="->", arrowsize=15)
        nx.draw_networkx_labels(self.graph, pos)
        plt.axis("off")
        plt.savefig(save_path, bbox_inches="tight")
        plt.close()
    
    def get_all_paths_from_graph(self, target, max_depth=6):
        paths = []

        def dfs(node, path, depth):
            if depth > max_depth:
                return

            preds = list(self.graph.predecessors(node))

            if not preds:
                paths.append(path)
                return

            for p in preds:
                if p in path:
                    continue  # avoid cycles
                dfs(p, path + [p], depth + 1)

        dfs(target, [target], 0)
        return paths
    
    def get_feedback_loops(self, max_len=6):
        loops = list(nx.simple_cycles(self.graph))
        return [l for l in loops if len(l) <= max_len]

    def _line_text(self, lineno):
        if lineno is None or lineno < 1 or lineno > len(self.source_lines):
            return ""
        return self.source_lines[lineno - 1].strip()

    def get_path_assignment_lines(self, path):
        assignments = []
        seen = set()

        for signal in path:
            for lineno, _ in self.assign_map.get(signal, []):
                key = (signal, lineno)
                if key in seen:
                    continue
                seen.add(key)
                assignments.append({
                    "signal": signal,
                    "line": lineno,
                    "code": self._line_text(lineno),
                })

        return assignments
    
    def build_dependency_context(self, buggy_signals, graph_save_path=None):
        if graph_save_path:
            self.visualize_graph(graph_save_path, buggy_signals)

        context = {
            "buggy_signals": buggy_signals,
            "paths": {},
            "dependency_edges": {},
            "loops": [],
        }

        for sig in buggy_signals:
            raw_paths = self.get_all_paths_from_graph(sig, max_depth=6)
            unique_paths = sorted(set(tuple(p) for p in raw_paths), key=len)
            limited_paths = unique_paths[:5]
            formatted = [format_path(list(p)) for p in limited_paths]
            context["paths"][sig] = formatted
            # context["path_assignment_lines"][sig] = [
            #     {
            #         "path": format_path(list(p)),
            #         "assignments": self.get_path_assignment_lines(p),
            #     }
            #     for p in limited_paths
            # ]
            context["dependency_edges"][sig] = self.export_llm_dependencies(sig)["dependencies"]

        loops = self.get_feedback_loops()
        context["loops"] = [" -> ".join(l) for l in loops]

        # context["ranked_roots"] = self.rank_root_signals(context["paths"])

        return context

# def find_candidate_bug_lines(verilog_file, buggy_signals, graph_save_path=None):
#     tracer = VerilogTracer(verilog_file)
#     tracer.build_graph()
#     if graph_save_path:
#         tracer.visualize_graph(graph_save_path, buggy_signals)
#     suspicious = tracer.trace_signals(buggy_signals)
#     with open(verilog_file, "r") as f:
#         lines = f.readlines()
#     results = []
#     seen = set()
#     for sig, ln in suspicious:
#         if ln and ln <= len(lines) and ln not in seen:
#             results.append((ln, lines[ln - 1]))
#             seen.add(ln)
#     return sorted(results, key=lambda x: x[0], reverse=True)



# def build_dependency_context(verilog_file, buggy_signals, graph_save_path=None):
#     tracer = VerilogTracer(verilog_file)
#     tracer.build_graph()

#     if graph_save_path:
#         tracer.visualize_graph(graph_save_path, buggy_signals)

#     per_signal = {}

#     for target in buggy_signals:
#         deps = tracer.export_llm_dependencies(target)
#         per_signal[target] = deps["dependencies"]

#     return {
#         "buggy_signals": buggy_signals,
#         "per_signal_dependencies": per_signal
#     }

