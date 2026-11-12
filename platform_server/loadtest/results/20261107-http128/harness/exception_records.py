"""Count logger records, not duplicate source/exception lines within one traceback."""
import re
START=re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d+)\s+\w+\s+')
ACCESS=re.compile(r'^\d+\.\d+\.\d+\.\d+:\d+ - - \[')
FINAL='autobahn.exception.Disconnected: Attempt to send on a closed protocol'
def exception_records(text,name):
 records=[];current=None
 for n,line in enumerate(text.splitlines(),1):
  match=START.match(line)
  if match or ACCESS.match(line):
   if current and current['matched']:records.append({k:v for k,v in current.items() if k!='matched'})
   current={'log':name,'line':n,'at':match[1],'exception':'autobahn.exception.Disconnected','matched':False} if match else None
  if line.startswith(FINAL):
   assert current is not None,('Exception without an identifiable logger record',name,n)
   current['matched']=True
 if current and current['matched']:records.append({k:v for k,v in current.items() if k!='matched'})
 return records
if __name__=='__main__':
 record='2026-11-06 16:00:00,001 ERROR Exception inside application\nTraceback\n    raise Disconnected("Attempt to send on a closed protocol")\n'+FINAL+'\n'
 assert len(exception_records(record+FINAL+'\n','test'))==1
 assert len(exception_records(record+record,'test'))==2
 assert not exception_records('2026-11-06 16:00:00,001 INFO '+FINAL+'\n','test')
 print('Exception-record self-checks passed: duplicate chain, separate records, source text')
