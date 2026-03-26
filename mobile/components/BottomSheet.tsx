import React, { useEffect, useRef, useState } from 'react';
import {
  Animated,
  Modal,
  StyleProp,
  StyleSheet,
  View,
  ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';

type BottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  sheetStyle?: StyleProp<ViewStyle>;
  backdropStyle?: StyleProp<ViewStyle>;
  contentStyle?: StyleProp<ViewStyle>;
  includeBottomInset?: boolean;
  baseBottomPadding?: number;
  dismissOnBackdropPress?: boolean;
};

const OPEN_DURATION_MS = 240;
const CLOSE_DURATION_MS = 200;
const ENTER_TRANSLATE_Y = 360;

export function BottomSheet({
  visible,
  onClose,
  children,
  sheetStyle,
  backdropStyle,
  contentStyle,
  includeBottomInset = true,
  baseBottomPadding = 18,
  dismissOnBackdropPress = true,
}: BottomSheetProps) {
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(visible);
  const translateY = useRef(new Animated.Value(ENTER_TRANSLATE_Y)).current;
  const backdropOpacity = useRef(new Animated.Value(0)).current;
  const animationCycleRef = useRef(0);

  useEffect(() => {
    animationCycleRef.current += 1;
    const cycle = animationCycleRef.current;

    if (visible) {
      setRendered(true);
      translateY.setValue(ENTER_TRANSLATE_Y);
      backdropOpacity.setValue(0);
      Animated.parallel([
        Animated.timing(translateY, {
          toValue: 0,
          duration: OPEN_DURATION_MS,
          useNativeDriver: true,
        }),
        Animated.timing(backdropOpacity, {
          toValue: 1,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start();
      return;
    }

    if (!rendered) {
      return;
    }

    Animated.parallel([
      Animated.timing(translateY, {
        toValue: ENTER_TRANSLATE_Y,
        duration: CLOSE_DURATION_MS,
        useNativeDriver: true,
      }),
      Animated.timing(backdropOpacity, {
        toValue: 0,
        duration: 160,
        useNativeDriver: true,
      }),
    ]).start(() => {
      if (animationCycleRef.current !== cycle) {
        return;
      }
      if (!visible) {
        setRendered(false);
      }
    });
  }, [backdropOpacity, rendered, translateY, visible]);

  if (!rendered) {
    return null;
  }

  return (
    <Modal transparent visible animationType="none" onRequestClose={onClose}>
      <Animated.View style={[styles.backdrop, backdropStyle, { opacity: backdropOpacity }]}>
        <Pressable
          style={StyleSheet.absoluteFill}
          onPress={dismissOnBackdropPress ? onClose : undefined}
        />
        <Animated.View
          style={[
            styles.sheet,
            sheetStyle,
            {
              transform: [{ translateY }],
            },
          ]}
        >
          <View
            style={[
              styles.content,
              contentStyle,
              {
                paddingBottom: baseBottomPadding + (includeBottomInset ? insets.bottom : 0),
              },
            ]}
          >
            {children}
          </View>
        </Animated.View>
      </Animated.View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.25)',
  },
  sheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
  },
  content: {
    paddingTop: 16,
    paddingHorizontal: 18,
  },
});
