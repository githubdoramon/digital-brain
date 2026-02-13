import React from 'react';
import {
  Platform,
  Pressable as NativePressable,
  PressableStateCallbackType,
  StyleSheet,
  StyleProp,
  ViewStyle,
} from 'react-native';

const DEFAULT_ANDROID_RIPPLE = {
  color: 'rgba(26,29,34,0.12)',
  borderless: false,
  foreground: true,
} as const;

type AppPressableProps = React.ComponentProps<typeof NativePressable>;
type AppPressableRef = React.ElementRef<typeof NativePressable>;

type Props = AppPressableProps & {
  enablePressedStyleOnAndroid?: boolean;
  clipRippleOnAndroid?: boolean;
};

export const AppPressable = React.forwardRef<AppPressableRef, Props>(
  (
    {
      android_ripple,
      style,
      enablePressedStyleOnAndroid = false,
      clipRippleOnAndroid = true,
      ...props
    },
    ref,
  ) => {
    const resolvedRipple =
      Platform.OS === 'android'
        ? android_ripple === null
          ? null
          : {
              ...DEFAULT_ANDROID_RIPPLE,
              ...android_ripple,
            }
        : android_ripple;

    const shouldClipRipple = Platform.OS === 'android' && !!resolvedRipple && clipRippleOnAndroid;

    const resolvedStyle =
      Platform.OS === 'android' &&
      !!resolvedRipple &&
      !enablePressedStyleOnAndroid &&
      typeof style === 'function'
        ? (state: PressableStateCallbackType): StyleProp<ViewStyle> =>
            [
              (style as (value: PressableStateCallbackType) => StyleProp<ViewStyle>)({
                ...state,
                pressed: false,
              }),
              shouldClipRipple && styles.androidRippleClip,
            ]
        : style;

    const finalStyle =
      typeof resolvedStyle === 'function'
        ? resolvedStyle
        : [resolvedStyle, shouldClipRipple && styles.androidRippleClip];

    return <NativePressable ref={ref} {...props} style={finalStyle} android_ripple={resolvedRipple} />;
  },
);

AppPressable.displayName = 'AppPressable';

const styles = StyleSheet.create({
  androidRippleClip: {
    overflow: 'hidden',
  },
});
