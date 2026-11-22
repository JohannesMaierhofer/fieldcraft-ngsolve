"""Round-trip, mesh binding, time-axis validation and the native command handoff."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

import numpy as np

from show_run import apply
from time_series import description, validate, write


class TimeSeries(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.nodes = np.array([7, 31], dtype=np.int64)
        self.digest = 'c' * 64
        self.mesh = {'mesh_sha256':np.array(self.digest),'node_ids':self.nodes}

    def make(self, shape=(2,3)):
        frames = [(np.float64(0),np.zeros(shape)), (.5,np.ones(shape))]
        return write(self.home, self.mesh, {'B':('T',frames)})[0]

    def change(self, path, fn):
        value = json.loads(path.read_bytes())
        fn(value)
        path.write_text(json.dumps(value))

    def test_vector_round_trip_and_native_command(self):
        item = self.make()
        validate(self.home,item,self.digest,self.nodes.tolist())
        fn = Mock(side_effect=[[{'id':'results.external.series'}],{}, {'shown':True}])
        run = {'mesh_sha256':self.digest,'series':[item]}
        self.assertEqual(apply(fn,self.home/'results.json',run,None,'y',False,None,series='B'),[{'shown':True}])
        self.assertEqual(fn.call_args.args[1]['params'], {'component':'y','path':str(self.home/'series-1.json')})

    def test_scalar_rejects_vector_component(self):
        item = self.make((2,))
        validate(self.home,item,self.digest,self.nodes.tolist())
        run = {'mesh_sha256':self.digest,'series':[item]}
        fn = Mock()
        with self.assertRaises(ValueError):
            apply(fn,self.home/'results.json',run,None,'x',False,None,series='B')
        fn.assert_not_called()

    def test_frames_require_matching_headers_labels_and_finite_values(self):
        item = self.make()
        path = self.home/'series-1-frame-0002.json'
        original = path.read_bytes()
        for key, bad in [('mesh_sha256','d'*64),('unit','A'),('name','X'),('components',1),
                         ('node_ids',[31,7]),('node_ids',[True,31]),('values',[0]*5),
                         ('values',[float('inf')]*6),('values',[False]*6)]:
            with self.subTest(key=key,bad=bad):
                path.write_bytes(original)
                self.change(path,lambda f:f.update({key:bad}))
                with self.assertRaises(ValueError):
                    validate(self.home,item,self.digest,self.nodes.tolist())

    def test_times_and_paths(self):
        item = self.make()
        path = self.home/item['file']
        original = path.read_bytes()
        for frame in [{'time':0,'file':'series-1-frame-0002.json'},
                      {'time':float('nan'),'file':'series-1-frame-0002.json'},
                      {'time':1,'file':'series-1-frame-0001.json'},
                      {'time':1,'file':'../outside.json'},
                      {'time':1,'file':'C:\\outside.json'}]:
            with self.subTest(frame=frame):
                path.write_bytes(original)
                self.change(path,lambda series:series['frames'].__setitem__(1,frame))
                with self.assertRaises(ValueError):
                    description(self.home,item,self.digest)
        path.write_bytes(original)
        first = self.home/'series-1-frame-0001.json'
        first.unlink()
        first.symlink_to(self.home/'series-1-frame-0002.json')
        with self.assertRaises(ValueError):
            validate(self.home,item,self.digest,self.nodes.tolist())

    def test_bounds(self):
        item = self.make()
        path = self.home/item['file']
        for n in [0,1,4097]:
            self.change(path,lambda series:series.update(frames=[{}]*n))
            with self.assertRaises(ValueError):
                description(self.home,item,self.digest)
