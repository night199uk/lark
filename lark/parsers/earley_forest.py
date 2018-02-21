from ..grammar import Rule
from ..lexer import Token
from ..common import is_terminal
from ..tree import Tree
from ..utils import convert_camelcase
from .earley_common import Column, Derivation, LR0
from collections import OrderedDict, Iterator
import sys
import re

class PackedNode(object):
    def __init__(self, parent, s, start, left, right):
        assert isinstance(parent, (SymbolNode, IntermediateNode))
        assert isinstance(s, LR0)
        assert isinstance(start, Column)
        assert isinstance(left, (TokenNode, SymbolNode, IntermediateNode)) or left is None
        assert isinstance(right, (TokenNode, SymbolNode, IntermediateNode)) or right is None
        self.parent = parent
        self.s = s
        self.start = start
        self.left = left
        self.right = right
        self.priority = 0

    def __eq__(self, other):
        if not isinstance(other, PackedNode):
            return False
        return self is other or (self.s == other.s and self.start == other.start and self.left == other.left and self.right == other.right)

    def __hash__(self):
        return hash((hash(self.s), self.start.i, hash(self.left), hash(self.right)))

    def __repr__(self):
        return "{%s, %d, %s, %s}" % (self.s, self.start.i, self.left, self.right)

class IntermediateNode(object):
    def __init__(self, s, start, end):
        assert isinstance(s, LR0)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        self.s = s
        self.start = start
        self.end = end
        self.children = None
        self.priority = 0

    def add_family(self, lr0, start, left, right):
        packed_node = PackedNode(self, lr0, start, left, right)
        if self.children is None:
            self.children = [ packed_node ]
        if packed_node not in self.children:
            self.children.append(packed_node)

    @property
    def is_ambiguous(self):
        return len(self.children) > 1

    def __eq__(self, other):
        if not isinstance(other, IntermediateNode):
            return False
        return self is other or (self.s == other.s and self.start == other.start and self.end == other.end)

    def __hash__(self):
        return hash((hash(self.s), self.start.i, self.end.i))

    def __repr__(self):
        return "[%s, %d, %d]" % (self.s, self.start.i, self.end.i)

class SymbolNode(object):
    def __init__(self, s, start, end):
        assert isinstance(s, LR0)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        self.s = s
        self.start = start
        self.end = end
        self.children = None
        self.priority = 0

    def add_family(self, lr0, start, left, right):
        # Note which production is responsible for this subtree,
        # to help navigate the tree in case of ambiguity
        packed_node = PackedNode(self, lr0, start, left, right)
        if self.children is None:
            self.children = [ packed_node ]
        if packed_node not in self.children:
            self.children.append(packed_node)

    @property
    def is_ambiguous(self):
        return len(self.children) > 1

    def __eq__(self, other):
        if not isinstance(other, SymbolNode):
            return False
        return self is other or (self.s == other.s and self.start == other.start and self.end == other.end)

    def __hash__(self):
        return hash((hash(self.s), self.start.i, self.end.i))

    def __repr__(self):
        return "(%s, %d, %d)" % (self.s.rule.origin, self.start.i, self.end.i)


class TokenNode(object):
    def __init__(self, token, start, end):
        assert isinstance(token, Token)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        self.token = token
        self.start = start
        self.end = end

    def __eq__(self, other):
        if not isinstance(other, TokenNode):
            return False
        return self is other or (self.token == other.token and self.start == other.start and self.end == other.end)

    def __hash__(self):
        return hash((self.token, self.start.i, self.end.i))

    def __repr__(self):
        return "(%s, %s, %s)" % (self.token, self.start.i, self.end.i)

class VirtualNode(object):
    def __init__(self, s, start, end):
        assert isinstance(s, LR0)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        self.s = s
        self.start = start
        self.end = end
        self.children = None
            
    def add_family(self, lr0, start, left, right):
        # Note which production is responsible for this subtree,
        # to help navigate the tree in case of ambiguity
        packed_node = PackedNode(self, lr0, start, left, right)
        if self.children is None:
            self.children = [ packed_node ]
        if packed_node not in self.children:
            self.children.append(packed_node)
        
    def add_unique_path(self, transitive, node):
        packed_node = None
        production = transitive.reduction.s.advance()
        if transitive.next_titem is not None:
            target = transitive.next_titem.reduction if transitive.next_titem.node is None else transitive.next_titem

            vn = VirtualNode(target.s, target.start, self.end)
            vn.add_unique_path(transitive.next_titem, node)

            if transitive.reduction.node is None:
                self.add_family(production, target.start, None, vn)
            else:
                self.add_family(production, target.start, transitive.reduction.node, vn)
            
        elif transitive.reduction.node is not None:
            self.add_family(production, transitive.reduction.start, transitive.reduction.node, node)
        else:
            self.add_family(production, transitive.reduction.start, None, node)

    @property   
    def is_ambiguous(self):
        return len(self.children) > 1
            
    def __eq__(self, other):
        if not isinstance(other, SymbolNode):
            return False
        return self is other or (self.s == other.s and self.start == other.start and self.end == other.end)

    def __hash__(self):
        return hash((hash(self.s), self.start.i, self.end.i))

    def __repr__(self):
        return "(%s, %d, %d)" % (self.s.rule.origin, self.start.i, self.end.i)

class Forest(object):
    def __init__(self):
        self.node_cache = {}
        self.token_cache = {}
        self.virtual_node_cache = {}

    def reset(self):
        pass

    def make_virtual_node(self, end, transitive, node):
        target = transitive.reduction if transitive.node is None else transitive
        label = (target.s, target.start.i, end.i)
        vn = self.virtual_node_cache.setdefault(label, VirtualNode(target.s, target.start, end))
        vn.add_unique_path(transitive, node)
        return vn

    def make_intermediate_or_symbol_node(self, lr0, start, end):
        assert isinstance(lr0, LR0)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        if lr0.is_complete:
            label = (lr0.rule.origin, start.i, end.i)
            node = self.node_cache.setdefault(label, SymbolNode(lr0, start, end))
        else:
            label = (lr0, start.i, end.i)
            node = self.node_cache.setdefault(label, IntermediateNode(lr0, start, end))
        return node

    def make_token_node(self, token, start, end):
        assert isinstance(token, Token)
        assert isinstance(start, Column)
        assert isinstance(end, Column)
        label = (token, start.i, end.i)
        return self.token_cache.setdefault(label, TokenNode(token, start, end))

    def make_null_node(self, lr0, column):
        assert isinstance(lr0, LR0)
        assert isinstance(column, Column)
        if lr0.is_complete:
            label = (lr0.rule.origin, column.i, column.i)
            node = self.node_cache.setdefault(label, SymbolNode(lr0, column, column))
        else:
            label = (lr0, column.i, column.i)
            node = self.node_cache.setdefault(label, IntermediateNode(lr0, column, column))
        node.add_family(lr0, column, None, None)
        return node
            
    def write_gexf(self):
        from lxml import etree
        root = etree.Element('gexf')
        graph = etree.Element('graph')
        nodes = etree.Element('nodes')
        edges = etree.Element('edges')
        root.append(graph)
        graph.append(nodes)
        graph.append(edges)

        nodes.append(etree.Element('node', id=str(0), label='{},{},{}'.format(0, 0, 0)))
        for i in self.node_cache:
            node = self.node_cache[i]
            node_id = str(id(node))
            nodes.append(etree.Element('node', id=node_id, label='{},{},{}'.format(node.s, node.start.i, node.end.i), type='symbol'))
            children = self.node_cache[i].children
            for packed_node in children:
                packed_node_id = str(id(packed_node))
                nodes.append(etree.Element('node', id=packed_node_id, label='{},{}'.format(packed_node.s, packed_node.start.i), type='packed'))
                edges.append(etree.Element('edge', id=packed_node_id, source=node_id, target=packed_node_id))
                down_id    = str(0) if packed_node.right is None else str(id(packed_node.right))
                edges.append(etree.Element('edge', id=str(hash((packed_node_id, down_id))), source=packed_node_id, target=down_id))
                if packed_node.left:
                    forward_id = str(0) if packed_node.left is None else str(id(packed_node.left))
                    edges.append(etree.Element('edge', id=str(hash((packed_node_id, forward_id))), source=packed_node_id, target=forward_id))

        for i in self.token_cache:
            token = self.token_cache[i]
            token_id = str(id(token))
            nodes.append(etree.Element('node', id=token_id, label='{},{},{}'.format(token.token, token.start.i, token.end.i), type='token'))

        f = open('test.gexf', 'w')
        et = etree.ElementTree(root)
        et.write(f, pretty_print = True)
        f.close()

class ForestVisitor(object):
    def __init__(self, forest, root):
        self.forest = forest
        self.root = root

    def walk_norecurse(self):
        visiting = set([])
        input_stack = [self.root]
        while input_stack:
            current = next(reversed(input_stack))

            if isinstance(current, Iterator):
                try:
                    input_stack.append(next(current))
                except StopIteration:
                    input_stack.pop()
                continue
            elif current == None:
                input_stack.pop()
                continue

            # Not the prettiest, but the fastest by far.
            if id(current) in [ id(x) for x in input_stack[:-1] ]:
                input_stack.pop()
                continue
#                raise Exception

            function_name = "visit_" + convert_camelcase(current.__class__.__name__)
            if id(current) in visiting:
                function_name += "_out"
            else:
                function_name += "_in"

            f = None
            try:
                f = getattr(self, function_name)
            except AttributeError:
                pass

            if id(current) in visiting:
                if f:
                    f(current)
                input_stack.pop()
                visiting.remove(id(current))
                continue
            else:
                if f:
                    input_stack.append(f(current))
                visiting.add(id(current))
                continue

        return self.result

    def go(self):
        result = self.walk_norecurse()
        return result # if result is not None else Tree(start_symbol, [])

class ForestToTreeVisitor(ForestVisitor):
    def __init__(self, forest, root):
        super(ForestToTreeVisitor, self).__init__(forest, root)
        self.output_stack = []
        self.result = None

    def visit_token_node_in(self, node):
        if self.output_stack:
            self.output_stack[-1].children.append(node.token)
        return None

    def visit_symbol_node_in(self, node):
        if node.is_ambiguous:
            ambiguous = Tree('_ambig', [])
            if self.output_stack:
                self.output_stack[-1].children.append(ambiguous)
            self.output_stack.append(ambiguous)
        else:
            drv = Derivation(node.s.rule, []) 
            if self.output_stack:
                self.output_stack[-1].children.append(drv)
            self.output_stack.append(drv)
        return iter(node.children)

    def visit_symbol_node_out(self, node):
        node.priority = node.s.rule.options.priority if node.s.rule.options and node.s.rule.options.priority is not None else 0
        node.priority += sum(child.priority for child in node.children)
        self.result = self.output_stack.pop()

    def visit_intermediate_node_in(self, node):
        return iter(node.children)

    def visit_intermediate_node_out(self, node):
        node.priority = node.s.rule.options.priority if node.s.rule.options and node.s.rule.options.priority is not None else 0
        node.priority += sum(child.priority for child in node.children)

    def visit_packed_node_in(self, node):
        if node.parent.is_ambiguous:
            drv = Derivation(node.s.rule, []) 
            if self.output_stack:
                self.output_stack[-1].children.append(drv)
            self.output_stack.append(drv)
        return iter([node.left, node.right])

    def visit_packed_node_out(self, node):
        node.priority = node.s.rule.options.priority if node.s.rule.options and node.s.rule.options.priority is not None else 0
        if node.left is None and node.right is None:
            ### Special case: ensure NULL nodes (which have None for both left and right pointers) are choice of last resort
            node.priority = -1
        else:
            node.priority += sum(child.priority if isinstance(child, (SymbolNode, IntermediateNode)) else 0 for child in [node.left, node.right])

        if node.parent.is_ambiguous:
            drv = self.output_stack.pop()
            drv.priority = node.priority
