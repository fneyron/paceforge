// Exercise the actual App component; WebView and HealthKit themselves are mocks.
import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {View} from './native-web';
const w=window as any;
w.writes=[];w.scripts=[];w.collects=0;w.navigations=[];w.refreshes=[];w.registered=[];w.tasks={};w.healthWatchers=[];
// ?locked=1: the keychain is unreadable (iOS background launch on a locked phone) until unlock().
w.lockedSecure=location.search.includes('locked');w.unlock=()=>{w.lockedSecure=false;};
const data:Record<string,string>=sessionStorage.getItem('secure')?JSON.parse(sessionStorage.getItem('secure')!):location.search.includes('unpaired')?{}:{'paceforge.phone.v1':JSON.stringify({token:'fixture',user_id:1,name:'Athlete'}),'paceforge.phone.v1.auto':'true'};
const locked=()=>{if(w.lockedSecure)throw Error("Calling the 'getValueWithKeyAsync' function has failed\n→ Caused by: User interaction is not allowed.");};
export const AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY=1;
export const getItem=(k:string)=>{locked();return data[k]||null;};
export const getItemAsync=async(k:string)=>{locked();return data[k]||null;};
export const setItemAsync=async(k:string,v:string)=>{locked();if(w.failSecure)throw Error('secure failure');data[k]=v;sessionStorage.setItem('secure',JSON.stringify(data))};
export const deleteItemAsync=async(k:string)=>{locked();delete data[k];sessionStorage.setItem('secure',JSON.stringify(data))};
export const Paths={document:'file:///documents'};
export class File {
 uri:string;
 constructor(...parts:any[]){this.uri=parts.map(p=>typeof p==='string'?p:p.uri).join('/');}
 get exists(){return sessionStorage.getItem(this.uri)!==null;}
 get size(){return (sessionStorage.getItem(this.uri)||'').length;}
 async text(){return sessionStorage.getItem(this.uri)||'';}
 textSync(){return sessionStorage.getItem(this.uri)||'';}
 write(value:string){if(w.failDisk)throw Error('disk full');sessionStorage.setItem(this.uri,value);}
 delete(){sessionStorage.removeItem(this.uri);}
}
export const getRandomBytes=(n:number)=>crypto.getRandomValues(new Uint8Array(n));
export const CryptoDigestAlgorithm={SHA256:'SHA256'};
export const digestStringAsync=async()=> 'a'.repeat(64);
export const openAuthSessionAsync=async(url:string)=>{w.authSessions=[...(w.authSessions||[]),url];return w.authResult||{type:'cancel'};};export const authorize=async()=>{};export const authorizationNeeded=async()=>false;
// Background sync: task registration and HealthKit change subscriptions are recorded.
export const BackgroundTaskStatus={Restricted:1,Available:2};export const BackgroundTaskResult={Success:1,Failed:2};
export const defineTask=(name:string,run:any)=>{w.tasks[name]=run;};
export const getStatusAsync=async()=>BackgroundTaskStatus.Available;
export const registerTaskAsync=async(name:string,options:any)=>{w.registered.push(['register',name,options.minimumInterval]);};
export const unregisterTaskAsync=async(name:string)=>{w.registered.push(['unregister',name]);};
export const backgroundTypes=['HKQuantityTypeIdentifierBodyMass'];
export const protectedDataAvailable=async()=>!w.lockedSecure;
export const hasBackgroundAccess=async()=>true;
export const enableHealthBackground=async()=>{w.registered.push(['healthkit']);};
export const disableHealthBackground=async()=>{w.registered.push(['healthkit off']);};
export const watchHealthChanges=(change:()=>void)=>{w.healthWatchers.push(change);w.healthChange=()=>w.healthWatchers.at(-1)();return()=>{};};
export const changedMetrics=async(tokens:any)=>({daysBack:3,tokens});
export const collect=async()=>{w.collects++;if(w.holdHealth)await new Promise(resolve=>{w.releaseHealth=resolve});return{days:w.emptyHealth?[]:[{date:'2026-10-12',metric:'weight',value:64,unit:'kg',sources:['Fixture'],measured_at:'2026-10-12T08:00:00Z'}],complete:true,notices:[]}};
export const collectSleep=async()=>({nights:[],notices:w.partialHealth?['Lecture du sommeil interrompue.']:[],complete:!w.partialHealth});
w.fetch=async(url:string)=>{w.writes.push(url);if(w.failUpload)throw Error('Envoi de test interrompu');if(w.failStatus)return{ok:false,status:w.failStatus,headers:{get:()=>null},json:async()=>({})};return{ok:true,json:async()=>url.endsWith('/exchange')?{token:'fixture',user_id:1,name:'Athlete'}:url.endsWith('/token')?{token:'r'.repeat(64),user_id:1}:{received:w.emptyHealth?0:1,checked_at:new Date().toISOString(),latest:{weight:{value:64,measured_at:'2026-10-12T08:00:00Z'}}}}};
export const SafeAreaProvider=({children}:any)=><View style={{flex:1}}>{children}</View>;
export const SafeAreaView=({children,style}:any)=><View style={[style,{flex:1}]}>{children}</View>;
export const StatusBar=()=>null;
export const WebView=forwardRef((props:any,ref:any)=>{
 const current=useRef(props);current.current=props;
 const [actual,setActual]=useState(props.source.uri);
 useImperativeHandle(ref,()=>({injectJavaScript:(s:string)=>{
  w.scripts.push(s);
  if(s.includes("type:'offline-account'")) {
   const nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]);
   queueMicrotask(()=>w.webMessage({type:'offline-account',nonce,user_id:w.actual.includes('/auth/')?null:(w.accountId||1)}));
  }
  if(s.includes("type:'offline-plan'")) {
   const nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]);
   const routeId=Number(s.match(/routes\/(\d+)\/offline/)[1]);
   w.offlineReply=()=>w.webMessage({type:'offline-plan',nonce,plan:w.failOffline?null:{version:1,user_id:w.accountId||1,route_id:routeId,name:'Course de montagne',saved_at:'2026-10-12T20:00:00Z',race_date:'2026-10-20',start_s:75600,planned:true,distance_km:30,elevation_m:1100,points:[{name:'Col',km:10,clock_s:86460,kind:'full',crew:true,drop_bag:true,cutoff_clock:'02:00',cutoff_margin_s:7140,pinned:false,passed:false}]}});
   if(!w.holdOffline)queueMicrotask(()=>w.offlineReply());
  }
  if(s.includes('window.paceforgeTabs?.cancel?.()')) w.softEpoch=(w.softEpoch||0)+1;
  if(s.includes("type:'tab-navigation'")) {
   const nonce=JSON.parse(s.match(/nonce:("[^\"]+")/)[1]);
   const target=JSON.parse(s.match(/url:("[^\"]+")/)[1]);
   const epoch=w.softEpoch||0;
   w.finishSoft=()=>{
    if(epoch!==(w.softEpoch||0))return;
    if(!w.softTabs)w.webMessage({type:'tab-navigation',nonce,url:target,status:'fallback'});
    else {w.actual=target;setActual(target);w.webMessage({type:'tab-navigation',nonce,url:target,status:'ready'});}
   };
   w.softTasks=(w.softTasks||[]).concat([{nonce,target}]);
   if(w.commitSoftBeforeReady&&w.softTabs){
    w.actual=target;setActual(target);
    w.oldHistory=()=>{current.current.onLoadEnd({nativeEvent:{url:target}});current.current.onNavigationStateChange({url:target,loading:false});};
   }
   if(!w.holdSoft)queueMicrotask(()=>w.finishSoft());
  }
  if(s.includes("type:'navigation-ready'")) {
   const nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]);
   w.finishLeave=()=>w.webMessage({type:'navigation-ready',nonce,ok:!w.failLeave});
   if(!w.holdLeave)queueMicrotask(()=>w.finishLeave());
  }
  if(s.includes('session-unavailable')) w.nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]);
  if(s.includes('connections-unavailable')) { w.nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]); w.connectionNonce=w.nonce; }
  if(s.includes('window.paceforgeRefresh')){
   w.refreshes.push(s.includes('refresh(true)'));
   w.webMessage({type:'refresh-state',active:true});
   w.finishRefresh=()=>w.webMessage({type:'refresh-state',active:false});
   if(!w.holdCloud)queueMicrotask(()=>w.finishRefresh());
  }
 },stopLoading(){},reload(){w.reloads=(w.reloads||0)+1}}),[]);
 function commit(url:string){w.frameURL=url;w.actual=url;setActual(url);current.current.onNavigationStateChange({url,loading:false});current.current.onLoadEnd({nativeEvent:{url}});}
 useEffect(()=>{
  const url=props.source.uri;w.navigations.push(url);
  current.current.onLoadStart?.({nativeEvent:{url}});
  current.current.onNavigationStateChange({url,loading:true});
  w.finishNavigation=()=>commit(url);
  if(!w.holdNavigation)commit(url);
 },[props.source.uri]);
 w.actual=actual;w.source=props.source.uri;w.pullEnabled=props.pullToRefreshEnabled;
 w.webMessage=(data:any)=>current.current.onMessage({nativeEvent:{url:w.staleFrame?w.frameURL:w.actual,data:JSON.stringify({page_url:w.actual,...data})}});
 w.reply=(overrides:any={})=>{const url=new URL(actual),inline=!url.pathname.startsWith('/mobile/');return current.current.onMessage({nativeEvent:{url:w.staleFrame?w.frameURL:actual,data:JSON.stringify({type:'session',page_url:actual,nonce:inline?w.nonce:url.searchParams.get('nonce'),transport:inline?'inline':undefined,user_id:1,...overrides})}})};
 w.navigate=(url:string)=>commit(url);
 w.softWebNavigate=(url:string)=>{w.actual=url;setActual(url);w.oldHistory=()=>{current.current.onLoadEnd({nativeEvent:{url}});current.current.onNavigationStateChange({url,loading:false});};};
 w.fullNavigate=(url:string)=>{if(current.current.onShouldStartLoadWithRequest({url,navigationType:'other',isTopFrame:true})===false)return;current.current.onLoadStart?.({nativeEvent:{url}});commit(url);};
 w.followLink=(url:string)=>current.current.onShouldStartLoadWithRequest({url,navigationType:'click',isTopFrame:true});
 w.pull=()=>current.current.onShouldStartLoadWithRequest({url:actual,navigationType:'reload',isTopFrame:true});
 w.terminate=()=>current.current.onContentProcessDidTerminate();
 w.pageFail=()=>{current.current.onError();current.current.onLoadEnd({nativeEvent:{url:actual}});};
 return <View testID="mock-webview" style={[props.style,{backgroundColor:'#fff'}]}><span id="document-text">{actual.includes('/mobile/session')?'Compte vérifié':actual.includes('/auth')?'Connexion au compte':'Tableau de bord Santé'}</span></View>;
});
