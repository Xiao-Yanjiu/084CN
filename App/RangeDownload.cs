using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Security.Cryptography;
namespace CN084 {

public sealed class DownloadMeter:IDisposable {
 readonly string name;readonly long total;readonly Action<string,double> report;readonly object sync=new object();readonly System.Diagnostics.Stopwatch clock=System.Diagnostics.Stopwatch.StartNew();Timer timer;long received,progress,lastReceived;double lastTime,rate;bool closed;
 public DownloadMeter(string name,long total,Action<string,double> report){this.name=name;this.total=total;this.report=report;timer=new Timer(_=>Tick(),null,500,500);}
 public void Network(long bytes){Interlocked.Add(ref received,bytes);}
 public void Progress(long bytes){Interlocked.Exchange(ref progress,bytes);}
 void Tick(){lock(sync){if(closed)return;double now=clock.Elapsed.TotalSeconds,elapsed=now-lastTime;if(elapsed<0.05)return;long current=Interlocked.Read(ref received);double instant=(current-lastReceived)/elapsed;rate=lastTime==0?instant:rate*0.5+instant*0.5;lastReceived=current;lastTime=now;long done=Math.Max(0,Math.Min(total>0?total:long.MaxValue,Interlocked.Read(ref progress)));string speed=(rate/1048576.0).ToString("F2",System.Globalization.CultureInfo.InvariantCulture)+" MB/s";string eta=rate>1024&&total>done?TimeSpan.FromSeconds(Math.Min(359999,(total-done)/rate)).ToString(@"hh\:mm\:ss"):"—";try{report("正在下载 "+name+"\n"+(done/1048576.0).ToString("F1",System.Globalization.CultureInfo.InvariantCulture)+" / "+(total>0?(total/1048576.0).ToString("F1",System.Globalization.CultureInfo.InvariantCulture):"未知")+" MB · "+speed+" · 预计 "+eta,total>0?done*100.0/total:-1);}catch(Exception ex){Paths.Log("Download progress: "+ex.Message);}}}
 public void Dispose(){lock(sync){closed=true;}timer.Dispose();}
}

public static class RangeDownload {
 static HttpClient client=new HttpClient(new HttpClientHandler{AllowAutoRedirect=true}){Timeout=Timeout.InfiniteTimeSpan};
 static string Hash(string path){using(var sha=SHA256.Create())using(var f=File.OpenRead(path))return BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();}
 public static async Task Run(string url,string path,long total,string expectedHash,Action<string,double> report,CancellationToken token){
  ServicePointManager.DefaultConnectionLimit=Math.Max(16,ServicePointManager.DefaultConnectionLimit);Directory.CreateDirectory(Path.GetDirectoryName(path));
  if(File.Exists(path)&&(total<=0||new FileInfo(path).Length==total)&&await Task.Run(()=>Hash(path),token)==expectedHash){report("已校验："+Path.GetFileName(path),100);return;}
  if(total<=0){using(var request=new HttpRequestMessage(HttpMethod.Get,url)){request.Headers.Range=new System.Net.Http.Headers.RangeHeaderValue(0,0);using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(token)){timeout.CancelAfter(45000);using(var response=await client.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,timeout.Token)){response.EnsureSuccessStatusCode();var r=response.Content.Headers.ContentRange;if(response.StatusCode!=HttpStatusCode.PartialContent||r==null||!r.Length.HasValue)throw new IOException("服务器不支持安全分段下载");total=r.Length.Value;}}}}
  const long chunk=8L*1024*1024;int count=checked((int)((total+chunk-1)/chunk));string folder=path+".parts";
  if(!Path.GetFullPath(folder).StartsWith(Path.GetFullPath(Paths.Engines)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new IOException("分片目录越界");
  Directory.CreateDirectory(folder);string signature=Path.Combine(folder,"signature");
  if(File.Exists(signature)&&File.ReadAllText(signature)!=expectedHash){foreach(var file in Directory.GetFiles(folder,"*.bin"))File.Delete(file);}
  File.WriteAllText(signature,expectedHash);long done=0;long[] inflight=new long[count];using(var meter=new DownloadMeter(Path.GetFileName(path),total,report)){var gate=new SemaphoreSlim(8,8);var lastReport=DateTime.MinValue;object reportLock=new object();
  Action update=()=>{long active=0;for(int i=0;i<count;i++)active+=Interlocked.Read(ref inflight[i]);meter.Progress(Interlocked.Read(ref done)+active);};
  var tasks=Enumerable.Range(0,count).Select(async index=>{long start=index*chunk,end=Math.Min(total,start+chunk)-1,size=end-start+1;string part=Path.Combine(folder,index.ToString("D5")+".bin");if(File.Exists(part)&&new FileInfo(part).Length==size){Interlocked.Add(ref done,size);update();return;}await gate.WaitAsync(token);try{for(int attempt=0;;attempt++){token.ThrowIfCancellationRequested();string temp=part+".download";try{using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(token)){timeout.CancelAfter(120000);using(var request=new HttpRequestMessage(HttpMethod.Get,url)){request.Headers.Range=new System.Net.Http.Headers.RangeHeaderValue(start,end);using(var response=await client.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,timeout.Token)){response.EnsureSuccessStatusCode();var range=response.Content.Headers.ContentRange;if(response.StatusCode!=HttpStatusCode.PartialContent||range==null||range.From!=start||range.To!=end||range.Length!=total)throw new IOException("下载分片范围不匹配");using(var input=await response.Content.ReadAsStreamAsync())using(var output=new FileStream(temp,FileMode.Create,FileAccess.Write,FileShare.None,1024*1024,true)){byte[] buffer=new byte[256*1024];long received=0;int n;while((n=await input.ReadAsync(buffer,0,buffer.Length,timeout.Token))>0){await output.WriteAsync(buffer,0,n,timeout.Token);received+=n;meter.Network(n);Interlocked.Exchange(ref inflight[index],received);update();if(received>size)throw new IOException("下载分片超出长度");}if(received!=size)throw new IOException("下载分片不完整");}}}}if(File.Exists(part))File.Delete(part);File.Move(temp,part);Interlocked.Add(ref done,size);Interlocked.Exchange(ref inflight[index],0);update();break;}catch(Exception){Interlocked.Exchange(ref inflight[index],0);update();if(token.IsCancellationRequested)throw new OperationCanceledException(token);if(attempt>=4)throw;report("网络波动，正在重试分片 "+(index+1)+" / "+count,done*100.0/total);}await Task.Delay(1000*(attempt+1),token);}}finally{gate.Release();}}).ToArray();
  await Task.WhenAll(tasks);}
  token.ThrowIfCancellationRequested();report("正在合并并校验："+Path.GetFileName(path),-1);string combined=path+".combining";string digest;
  using(var sha=SHA256.Create())using(var output=new FileStream(combined,FileMode.Create,FileAccess.Write,FileShare.None)){byte[] buffer=new byte[1024*1024];for(int i=0;i<count;i++){token.ThrowIfCancellationRequested();using(var input=File.OpenRead(Path.Combine(folder,i.ToString("D5")+".bin"))){int n;while((n=input.Read(buffer,0,buffer.Length))>0){output.Write(buffer,0,n);sha.TransformBlock(buffer,0,n,null,0);}}}sha.TransformFinalBlock(new byte[0],0,0);digest=BitConverter.ToString(sha.Hash).Replace("-","").ToLowerInvariant();}
  if(digest!=expectedHash){File.Delete(combined);foreach(var f in Directory.GetFiles(folder,"*.bin"))File.Delete(f);throw new IOException("分段下载 SHA256 校验失败，已清除异常分片，可重试。");}
  if(File.Exists(path))File.Delete(path);File.Move(combined,path);foreach(var f in Directory.GetFiles(folder,"*.bin"))File.Delete(f);report("已校验："+Path.GetFileName(path),100);
 }
}
}
