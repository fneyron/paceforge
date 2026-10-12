export * from 'react-native-web';
export const Platform = { OS:'ios', select:(v:any)=>v.ios??v.default };
export const AppState = { addEventListener:(_:string,cb:any)=>{(window as any).foreground=()=>cb('active');return{remove(){}}} };
export const Linking = {getInitialURL:async()=>null,addEventListener:()=>({remove(){}}),openURL:async()=>{}};
