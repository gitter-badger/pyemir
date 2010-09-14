#
# Copyright 2008-2010 Sergio Pascual
# 
# This file is part of PyEmir
# 
# PyEmir is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# PyEmir is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with PyEmir.  If not, see <http://www.gnu.org/licenses/>.
# 

try:
    import json
except ImportError:
    import simplejson as json

from pyfits import HDUList

from generic import generic
from jsonserializer import to_json
from numina.recipes import RecipeResult

@generic
def store(obj, where=None):
    raise TypeError(repr(type(obj)) + ' is not storable')

@store.register(HDUList)
def _store_fits(obj, where=None):
    '''Save to disk an HDUList structure.'''
    where = where or obj['primary'].header.get('FILENAME', 'file.fits')
    obj.writeto(where, clobber=True, output_verify='ignore')

@store.register(RecipeResult)
def _store_rr(obj, where):
    '''Save to disk a RecipeResult.'''
    
    external = []
    
    try:
        # Iterator for dictionaries        
        iobj = obj.itervalues()
    except AttributeError:
        # All the rest
        iobj = iter(obj)
    
    for val in iobj:
        t = type(val)
        
        if t is dict:
            _store_rr(val, where)
        elif t is list:
            _store_rr(val, where)
        elif store.is_registered(t):
            filename = generate_fname(val)
            external.append((filename, val))
            val = '<file>: %s' % filename
        else:
            pass
        
    f = open(where, 'w+') 
    try:
        json.dump(obj, f, default=to_json, indent=1, encoding='utf-8')
    finally:
        f.close()
        
    for filename, obj in external:
        store(obj, filename)

@generic
def generate_fname(obj):
    raise TypeError('A filename cannot be generated for % s' % repr(type(obj)))

@generate_fname.register(HDUList)
def _generate_fits(obj):
    '''Generate a filename for a HDUList structure.'''
    return obj['primary'].header.get('FILENAME', 'file.fits')
