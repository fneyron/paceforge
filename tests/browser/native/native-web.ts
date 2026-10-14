export * from 'react-native-web';
export const Platform = { OS:'ios', select:(v:any)=>v.ios??v.default };
// ?state=background starts like an iOS background launch (HealthKit, periodic task): no screen shown.
const appListeners=new Set<(state:string)=>void>();
export const AppState = { currentState:new URLSearchParams(location.search).get('state')||'active',
 addEventListener:(_:string,cb:any)=>{appListeners.add(cb);return{remove(){appListeners.delete(cb)}}} };
const change=(state:string)=>{AppState.currentState=state;appListeners.forEach(cb=>cb(state));};
(window as any).foreground=()=>change('active');(window as any).background=()=>change('background');
export const Linking = {getInitialURL:async()=>null,addEventListener:()=>({remove(){}}),openURL:async()=>{}};
