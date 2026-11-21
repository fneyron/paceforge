export * from 'react-native-web';
export const Platform = { OS:'ios', select:(v:any)=>v.ios??v.default };
// ?state=background starts like an iOS background launch (HealthKit, periodic task): no screen shown.
// ?missed=1: the app became active while it was starting, before any listener (no change event).
const params=new URLSearchParams(location.search),appListeners=new Set<(state:string)=>void>();
export const AppState = { currentState:params.get('state')||'active',
 addEventListener:(_:string,cb:any)=>{if(params.has('missed'))AppState.currentState='active';appListeners.add(cb);return{remove(){appListeners.delete(cb)}}} };
const change=(state:string)=>{AppState.currentState=state;appListeners.forEach(cb=>cb(state));};
(window as any).foreground=()=>change('active');(window as any).background=()=>change('background');
// Links handed to the system are recorded; ?nocalendar=1: no app takes them (openURL rejects).
(window as any).opened=[];
export const Linking = {getInitialURL:async()=>null,addEventListener:()=>({remove(){}}),openURL:async(url:string)=>{(window as any).opened.push(url);if(params.has('nocalendar'))throw Error('no handler');}};
