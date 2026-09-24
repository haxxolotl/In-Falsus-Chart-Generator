"""Append material locations without rebuilding existing Addressables entries."""
import io,struct
from addressablestools.binary import CatalogBinaryReader,CatalogBinaryHeader
from addressablestools.decoder import SerializedObjectDecoder

class CatalogAppender:
 def __init__(self,raw,fallback):
  self.original=raw;self.data=bytearray(raw);self.reader=CatalogBinaryReader(io.BytesIO(raw),_buffer=raw)
  self.header=CatalogBinaryHeader.read(self.reader);self.pairs=list(zip(*[iter(self.reader.read_offset_array(self.header.keys_offset))]*2))
  self.index={SerializedObjectDecoder.decode_v2(self.reader,k):(k,ls) for k,ls in self.pairs}
  k,ls=self.index[fallback];self.key_type=struct.unpack_from('<I',raw,k)[0]
  loc=self.reader.read_offset_array(ls)[0];self.mat=list(struct.unpack_from('<4Ii2I',raw,loc))
  self.deps=self.reader.read_offset_array(self.mat[3]);self.bundle=list(struct.unpack_from('<4Ii2I',raw,self.deps[0]))
  self.options_type,self.options_offset=struct.unpack_from('<2I',raw,self.bundle[5]);self.options=list(struct.unpack_from('<5I',raw,self.options_offset))
 def block(self,raw):
  self.data.extend(bytes((-len(self.data))%4));off=len(self.data);self.data.extend(raw);return off
 def string(self,value):
  raw=value.encode('utf-8');assert raw.isascii()
  return self.block(struct.pack('<i',len(raw))+raw)+4
 def array(self,values):return self.block(struct.pack('<I',len(values)*4)+struct.pack('<'+'I'*len(values),*values))+4
 def add(self,key,asset_path,bundle_file,bundle_name,bundle_size):
  assert key not in self.index
  opts=list(self.options);opts[1]=self.string(bundle_name);opts[2]=0;opts[3]=bundle_size
  opt_pos=self.block(struct.pack('<5I',*opts));opt_obj=self.block(struct.pack('<2I',self.options_type,opt_pos))
  bun=list(self.bundle);bun[0]=self.string(bundle_file);bun[1]=self.string('{UnityEngine.AddressableAssets.Addressables.RuntimePath}/StandaloneWindows64/'+bundle_file);bun[5]=opt_obj
  bun_pos=self.block(struct.pack('<4Ii2I',*bun))
  mat=list(self.mat);mat[0]=self.string(asset_path);mat[1]=mat[0];mat[3]=self.array([bun_pos,*self.deps[1:]])
  mat_pos=self.block(struct.pack('<4Ii2I',*mat));loc_list=self.array([mat_pos]);key_pos=self.string(key)
  obj_val=self.block(struct.pack('<IH',key_pos,0));key_obj=self.block(struct.pack('<2I',self.key_type,obj_val))
  self.pairs.append((key_obj,loc_list));self.index[key]=(key_obj,loc_list)
 def finish(self):
  index=self.array([v for pair in self.pairs for v in pair]);struct.pack_into('<I',self.data,8,index);return bytes(self.data)
