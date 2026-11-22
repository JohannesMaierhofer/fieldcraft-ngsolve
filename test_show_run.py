"""Tests for native field selection and the GUI's unsaved-document path."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from show_run import ControlError, apply, main, read_run


class ViewerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.model = self.root / 'a.fieldcraft'
        with zipfile.ZipFile(self.model, 'w') as archive:
            archive.writestr('mesh.bin', b'mesh')
        digest = hashlib.sha256(b'mesh').hexdigest()
        metadata = {'name':'B','unit':'T','file':'field-1.json'}
        (self.root/'results.json').write_text(json.dumps({'format':'fieldcraft-external-run/1','mesh_sha256':digest,
                    'model':str(self.model),'fields':[metadata],'integrals':[{'name':'W','unit':'J','value':1.0}]}))
        (self.root/'field-1.json').write_text(json.dumps({'format':'fieldcraft-nodal-field/1','name':'B',
             'components':3,'unit':'T','mesh_sha256':digest}))
        self.manifest, self.run = read_run(self.root)
        self.commands = [{'id':'results.external.contour'},{'id':'results.external.integrals'}]

    def test_fields_and_integrals_use_mesh_checked_commands_without_open(self):
        rpc = Mock(side_effect=[self.commands, {}, {'shown':True}, {'integrals':self.run['integrals']}])
        apply(rpc, self.manifest, self.run, 'B', 'z', True, None)
        requests=[(args[0],args[1]) for args,_ in rpc.call_args_list]
        self.assertEqual(requests[2],('engine.execute',{'command':'results.external.contour','params':{'path':str(self.root/'field-1.json'),'component':'z'}}))
        self.assertEqual(requests[3],('engine.execute',{'command':'results.external.integrals','params':{'path':str(self.manifest)}}))

    def test_wrong_mesh_stops_without_changing_document(self):
        rpc = Mock(side_effect=[self.commands, {}, ControlError('different mesh')])
        with self.assertRaisesRegex(ControlError,'different mesh'):
            apply(rpc,self.manifest,self.run,'B',None,False,None)
        self.assertFalse(any(a[0][0]=='ui.drop' for a in rpc.call_args_list))

    @patch('show_run.time.sleep',return_value=None)
    def test_explicit_open_leaves_unsaved_decision_to_user(self,_sleep):
        rpc = Mock(side_effect=[self.commands,{}, {'dropped':1}, {'prompt':{'unsaved':str(self.model)}}])
        with self.assertRaisesRegex(ControlError,'Entscheidung'):
            apply(rpc,self.manifest,self.run,'B',None,False,self.model)
        self.assertEqual([a[0][0] for a in rpc.call_args_list],['engine.commands','ui.inspect','ui.drop','ui.inspect'])

    @patch('show_run.time.sleep',return_value=None)
    def test_explicit_open_waits_for_job_and_routes_field(self,_sleep):
        rpc = Mock(side_effect=[self.commands,{}, {'dropped':1},{'busy':'open'},{'recent':[str(self.model)]},{'shown':True}])
        reply=apply(rpc,self.manifest,self.run,'B',None,False,self.model)
        self.assertEqual(reply,[{'shown':True}])

    def test_old_build_leaves_document_untouched(self):
        rpc=Mock(return_value=[])
        with self.assertRaisesRegex(ControlError,'Build'):
            apply(rpc,self.manifest,self.run,'B',None,False,self.model)
        self.assertEqual(rpc.call_count,1)

    def test_invalid_field_file_fails_before_network(self):
        self.run['fields'][0]['file']='../missing.json';rpc=Mock()
        with self.assertRaises((OSError,ValueError)):
            apply(rpc,self.manifest,self.run,'B',None,False,None)
        rpc.assert_not_called()

    @patch('show_run.Control')
    @patch('builtins.print')
    def test_list_does_not_contact_a_window(self,output,control):
        main([str(self.root),'--list']);control.assert_not_called()
        self.assertIn('B',output.call_args.args[0])

if __name__=='__main__':unittest.main()
