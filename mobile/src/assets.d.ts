// Metro resolves an image to its asset; the type is what <Image source> takes.
declare module '*.png' {
  const source: import('react-native').ImageSourcePropType;
  export default source;
}
