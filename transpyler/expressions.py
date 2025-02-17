import ast
import math
from itertools import product
import _ast
from jinja2 import Template
from . import types
from .types import to_any, type_eval
from .utils import getvar
from .core import visitor, op_to_str
from .side_effects import side_effect


@visitor
def un_op(self, tree: _ast.UnaryOp):
    """Unary operations(not...)"""
    el = self.visit(tree.operand)
    _type = el_type = el.type
    tmp = 'un_op'
    op = op_to_str(tree.op)
    overload = self.templates.get(f'{op}.{_type}')
    while el_type != 'any' and not overload:
        el_type = to_any(el_type)
        overload = self.templates.get(f'{op}.{el_type}')
    if overload:
        tmp = overload.get('code', tmp)
        _type = type_eval(
            overload,
            {'op': op, 'el': el}
        )
        side_effect(overload, {'op': op, 'el': el})
    return self.node(
        tmp=tmp,
        type=_type,
        parts={
            'op': self.templates['operators'].get(op, op),
            'el': el
        }
    )

@visitor
def bin_op(self, tree: _ast.BinOp):
    """Math operation(+, -, *, /...)"""
    return _bin_op(
        self,
        self.visit(tree.left),
        self.visit(tree.right),
        op_to_str(tree.op)
    )

@visitor
def bool_op(self, tree: _ast.BoolOp):
    """Boolean logic operation(or, and)"""
    els = list(map(self.visit, tree.values))
    op = op_to_str(tree.op)
    expr = _bin_op(self, els[0], els[1], op)
    for el in els[2:]:
        expr = _bin_op(self, expr, el, op)
    return expr

@visitor
def compare(self, tree: _ast.Compare):
    """Compare operation(==, !=, >, <, >=, <=...)"""
    f_el = self.visit(tree.left)
    els = list(map(self.visit, tree.comparators))
    ops = list(map(op_to_str, tree.ops))
    expr = _bin_op(self, f_el, els[0], ops[0])
    for l, r, op in zip(els[:-1], els[1:], ops[1:]):
        expr = _bin_op(self, expr, _bin_op(self, l, r, op), 'and')
    return expr

def _bin_op(self, left, right, op):
    tmp = 'bin_op'
    left_t = left.type
    right_t = right.type
    left_possible_types = [str(left_t)]
    right_possible_types = [str(right_t)]
    _type = 'None'
    while left_t != 'any':
        left_t = to_any(left_t)
        left_possible_types.append(str(left_t))
    while right_t != 'any':
        right_t = to_any(right_t)
        right_possible_types.append(str(right_t))
    possible_type_pairs = product(
        left_possible_types,
        right_possible_types
    )
    for left_t, right_t in possible_type_pairs:
        overload = self.templates.get(
            f'{left_t}.{op}.{right_t}'
        )
        if overload:
            tmp = overload.get('code', tmp)
            side_effect(
                overload,
                {'left': left, 'right': right}
            )
            _type = types.type_eval(
                overload.get('type', 'None'),
                {'left': left, 'right': right}
            )
            break
    return self.node(
        parts={
            'left': left,
            'right': right,
            'op': self.templates['operators'].get(op, op)
        },
        tmp=tmp,
        type=_type
    )

def match_args(macro, args):
    """
    Match args names with their values
    """
    if 'args' not in macro:
        return {}
    return dict(list(zip(macro['args'], args)))

@visitor
def attribute(self, tree: _ast.Attribute, args=None, call=False):
    tmp = 'callmethod' if call else 'getattr'
    obj = self.visit(tree.value)
    _type = 'None'
    attr = tree.attr
    macro = ''
    parts = {'obj': obj, 'attr': attr, 'args': args}
    if isinstance(obj.type, types.Module):
        macro = self.templates[obj.type.name].get(attr)
    if not macro:
        obj_type = obj.type
        macro = self.templates.get(f'{obj_type}.{attr}')
        while obj_type != 'any' and not macro:
            obj_type = to_any(obj_type)
            macro = self.templates.get(f'{obj_type}.{attr}')
    if macro:
        parts['attr'] = macro.get('alt_name', attr)
        tmp = macro.get('code', tmp)
        parts.update(match_args(macro, args))
        side_effect(macro, parts)
        _type = types.type_eval(
            macro.get('rettype' if call else 'type', 'None'),
            parts
        )
    return self.node(
        type=_type,
        tmp=tmp,
        parts=parts
    )

@visitor
def function_call(self, tree: _ast.Call):
    args = [self.visit(a) for a in tree.args]
    if isinstance(tree.func, _ast.Attribute):
        return self.attribute(tree.func, args=args, call=True)
    func = self.visit(tree.func)
    ret_type = 'None'
    tmp = 'callfunc'
    parts = {'func': func, 'args': args}
    if func.type == 'class':
        tmp = 'new'
    if (isinstance(tree.func, _ast.Name)
        and tree.func.id in self.templates):
        macro = self.templates.get(tree.func.id)
        parts.update(match_args(macro, args))
        tmp = macro.get('code', 'callfunc')
        side_effect(macro, parts)
        _type = types.type_eval(
            macro.get('rettype', 'None'),
            parts
        )
    return self.node(
        tmp=tmp,
        type=ret_type,
        parts=parts
    )

@visitor
def _list(self, tree: _ast.List):
    elements = list(map(self.visit, tree.elts))
    if len(elements) > 0:
        el_type = elements[0].type
    else:
        el_type = 'generic'
    return self.node(
        tmp='list',
        type=types.List(el_type),
        parts={'ls': elements}
    )

@visitor
def _dict(self, tree: _ast.Dict):
    keys = list(map(self.visit, tree.keys))
    values = list(map(self.visit, tree.values))
    if len(keys):
        el_type = values[0].type
        key_type = keys[0].type
    else:
        el_type = 'generic'
        key_type = 'generic'
    return self.node(
        tmp='dict',
        type=types.Dict(key_type, el_type),
        parts={
            'keys': keys,
            'values': values,
            'keys_val': list(zip(keys, values))
        }
    )

@visitor
def slice(self, tree: _ast.Subscript):
    obj = self.visit(tree.value)
    _slice = tree.slice
    if not isinstance(_slice, _ast.Slice):
        return self.node(
            type=getattr(obj, 'el_type', 'None'),
            tmp='index',
            parts={'obj': obj, 'key': self.visit(_slice)}
        )
    return self.node(
        tmp = 'slice',
        type = obj.type,
        parts = {
            'obj': obj,
            'low': self.visit(
                _slice.lower or ast.Constant(value=0)
            ),
            'up': self.visit(
                _slice.upper or ast.Call(
                    func=ast.Name(id='len', ctx=_ast.Load),
                    args=[tree.value]
            )),
            'step': self.visit(
                _slice.step or ast.Constant(value=1)
            )
        }
    )

@visitor
def name(self, tree: _ast.Name):
    _name = tree.id
    _type = 'None'
    ctx = {
        _ast.Store: 'store',
        _ast.Load: 'load'
    }.get(type(tree.ctx))
    var_info = getvar(self, _name)
    if _name == 'self':
        _name = self.templates.get('self', 'self')
    elif var_info:
        _type = var_info['type']
    elif _name in self.templates:
        macr = self.templates[_name]
        _type = macr.get('type', _type)
        if _type == 'module':
            _type = types.Module(_name)
        _name = macr.get('alt_name', _name)
    return self.node(
        type=_type,
        tmp='name',
        parts={
            'name': _name,
            'ctx': ctx,
            'own': var_info.get('own')
        }
    )

@visitor
def const(self, tree: _ast.Constant):
    _val = tree.value
    _type = str(type(_val))[8:-2]
    parts={'val': _val}
    if isinstance(_val, float):
        parts |= {'parts': math.modf(_val)}
    return self.node(
        type=_type,
        tmp=_type.capitalize(),
        parts=parts
    )

@visitor
def none(self, tree: type(None)):
    return self.node(
        tmp='None',
        type='None'
    )
