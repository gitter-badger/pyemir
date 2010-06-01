
import logging
import time

_logger = logging.getLogger('numina.node')

class Node(object):
    def __init__(self):
        super(Node, self).__init__()

    def mark_as_processed(self, img):
        pass

    def check_if_processed(self, img):
        return False
    
    def __call__(self, image):
        raise NotImplementedError

class SerialNode(Node):
    def __init__(self, nodeseq):
        super(SerialNode, self).__init__()
        self.nodeseq = nodeseq
        
    def __iter__(self):
        return self.nodeseq.__iter__()
    
    def __len__(self):
        return self.nodeseq.__len__()
    
    def __getitem__(self, key):
        return self.nodeseq[key]
    
    def __setitem__(self, key, value):
        self.nodeseq[key] = value

    def __call__(self, inp):
        for nd in self.nodeseq:
            if not nd.check_if_processed(inp):
                out = nd(inp)
                inp = out
            else:
                _logger.info('%s already processed by %s', inp, nd)
                out = inp
        return out

class AdaptorNode(Node):
    def __init__(self, work):
        '''work is a function object'''
        super(AdaptorNode, self).__init__()
        self.work = work

    def __call__(self, ri):
        return self.work(ri)

class IdNode(Node):
    def __init__(self):
        '''Identity'''
        super(IdNode, self).__init__()

    def __call__(self, ri):
        return ri

class ParallelAdaptor(Node):
    def __init__(self, nodeseq):
        super(ParallelAdaptor, self).__init__()
        self.nodeseq = nodeseq
        
    def obtain_tuple(self, arg):
        if isinstance(arg, tuple):
            return arg
        return (arg,)

    def __call__(self, arg):
        args = self.obtain_tuple(arg)
        result = tuple(func(arg) for func, arg in zip(self.nodeseq, args))
        return result

class Corrector(Node):
    def __init__(self, label=None, mark=True, dtype='float32'):
        super(Corrector, self).__init__()
        self.dtype = dtype
        self.mark = mark
        if not label:
            self.label = ('NUM', 'Numina comment')
        else:
            self.label = label

    def check_if_processed(self, img):
        if self.mark and img and img.meta.has_key(self.label[0]):
            return True
        return False

    def mark_as_processed(self, img):
        if self.mark:
            img.meta.update(self.label[0], time.asctime(), self.label[1])
