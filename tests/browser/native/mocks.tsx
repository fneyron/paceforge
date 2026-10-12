// Exercise the actual App component; WebView and HealthKit themselves are mocks.
import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {View} from './native-web';
const w=window as any;
w.writes=[];w.scripts=[];w.collects=0;w.navigations=[];w.refreshes=[];
const data:Record<string,string>=location.search.includes('unpaired')?{}:{'paceforge.phone.v1':JSON.stringify({token:'fixture',user_id:1,name:'Athlete'}),'paceforge.phone.v1.auto':'true'};
export const getItemAsync=async(k:string)=>data[k]||null;
export const setItemAsync=async(k:string,v:string)=>{data[k]=v};
export const deleteItemAsync=async(k:string)=>{delete data[k]};
export const getRandomBytes=(n:number)=>crypto.getRandomValues(new Uint8Array(n));
export const CryptoDigestAlgorithm={SHA256:'SHA256'};
export const digestStringAsync=async()=> 'a'.repeat(64);
export const authorize=async()=>{};
export const collect=async()=>{w.collects++;if(w.holdHealth)await new Promise(resolve=>{w.releaseHealth=resolve});return{days:[{date:'2026-10-12',metric:'weight',value:64,unit:'kg',sources:['Fixture'],measured_at:'2026-10-12T08:00:00Z'}],complete:true,notices:[]}};
export const collectSleep=async()=>({nights:[],notices:[],complete:true});
w.fetch=async(url:string)=>{w.writes.push(url);if(w.failUpload)throw Error('Envoi de test interrompu');return{ok:true,json:async()=>({received:1,checked_at:new Date().toISOString(),latest:{weight:{value:64,measured_at:'2026-10-12T08:00:00Z'}}})}};
export const SafeAreaProvider=({children}:any)=><View style={{flex:1}}>{children}</View>;
export const SafeAreaView=({children,style}:any)=><View style={[style,{flex:1}]}>{children}</View>;
export const StatusBar=()=>null;
export const WebView=forwardRef((props:any,ref:any)=>{
 const current=useRef(props);current.current=props;
 const [actual,setActual]=useState(props.source.uri);
 useImperativeHandle(ref,()=>({injectJavaScript:(s:string)=>{
  w.scripts.push(s);
  if(s.includes('session-unavailable')) w.nonce=JSON.parse(s.match(/nonce:("[^"]+")/)[1]);
  if(s.includes('window.paceforgeRefresh'))w.refreshes.push(s.includes('refresh(true)'));
 },stopLoading(){},reload(){w.reloads=(w.reloads||0)+1}}),[]);
 useEffect(()=>{setActual(props.source.uri);w.navigations.push(props.source.uri);current.current.onNavigationStateChange({url:props.source.uri,loading:false});current.current.onLoadEnd({nativeEvent:{url:props.source.uri}});},[props.source.uri]);
 w.actual=actual;w.source=props.source.uri;w.pullEnabled=props.pullToRefreshEnabled;
 w.reply=(overrides:any={})=>{const url=new URL(actual),inline=!url.pathname.startsWith('/mobile/');return current.current.onMessage({nativeEvent:{url:actual,data:JSON.stringify({type:'session',nonce:inline?w.nonce:url.searchParams.get('nonce'),transport:inline?'inline':undefined,user_id:1,...overrides})}})};
 w.navigate=(url:string)=>{setActual(url);current.current.onNavigationStateChange({url,loading:false});current.current.onLoadEnd({nativeEvent:{url}})};
 w.pull=()=>current.current.onShouldStartLoadWithRequest({url:actual,navigationType:'reload',isTopFrame:true});
 w.terminate=()=>current.current.onContentProcessDidTerminate();
 return <View testID="mock-webview" style={[props.style,{backgroundColor:'#fff'}]}><span id="document-text">{actual.includes('/mobile/session')?'Compte vérifié':actual.includes('/auth')?'Connexion au compte':'Tableau de bord Santé'}</span></View>;
});
