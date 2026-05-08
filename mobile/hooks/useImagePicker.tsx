import React from 'react';
import * as ImagePicker from 'expo-image-picker';

import { ImageSourceSheet, type ImagePickSource } from '@/components/media/ImageSourceSheet';

function useImageSourceSheet(): {
  chooseSource: () => Promise<ImagePickSource | null>;
  sheet: React.ReactNode;
} {
  const [visible, setVisible] = React.useState(false);
  const resolverRef = React.useRef<((value: ImagePickSource | null) => void) | null>(null);

  const closeWithValue = React.useCallback((value: ImagePickSource | null) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setVisible(false);
  }, []);

  React.useEffect(() => {
    return () => {
      resolverRef.current?.(null);
      resolverRef.current = null;
    };
  }, []);

  const chooseSource = React.useCallback(() => {
    return new Promise<ImagePickSource | null>((resolve) => {
      resolverRef.current = resolve;
      setVisible(true);
    });
  }, []);

  const sheet = (
    <ImageSourceSheet
      visible={visible}
      onSelect={(source) => {
        closeWithValue(source);
      }}
      onClose={() => {
        closeWithValue(null);
      }}
    />
  );

  return { chooseSource, sheet };
}

export function useSingleImagePicker(): {
  pickSingleImage: () => Promise<ImagePicker.ImagePickerAsset | null>;
  imagePickerSheet: React.ReactNode;
} {
  const { chooseSource, sheet } = useImageSourceSheet();

  const pickSingleImage = React.useCallback(async (): Promise<ImagePicker.ImagePickerAsset | null> => {
    const source = await chooseSource();
    if (!source) {
      return null;
    }

    if (source === 'camera') {
      const cameraPermission = await ImagePicker.requestCameraPermissionsAsync();
      if (!cameraPermission.granted) {
        throw new Error('Allow camera access to capture a photo.');
      }

      const result = await ImagePicker.launchCameraAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.8,
        allowsEditing: false,
        base64: true,
      });
      return result.canceled || !result.assets?.length ? null : result.assets[0];
    }

    const libraryPermission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!libraryPermission.granted) {
      throw new Error('Allow photo library access to choose a photo.');
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.8,
      allowsEditing: false,
      base64: true,
    });
    return result.canceled || !result.assets?.length ? null : result.assets[0];
  }, [chooseSource]);

  return {
    pickSingleImage,
    imagePickerSheet: sheet,
  };
}
