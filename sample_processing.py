    vl_model = 'minicpm-v4.5:8b'
    
    try:
        if file_path.lower().endswith((".mp4", ".mov", ".avi")):
            # Video Processing
            temp_frame_dir = os.path.join("frames", os.path.basename(file_path))
            frame_paths = extract_frames(file_path, output_dir=temp_frame_dir, fps_sample_rate=1)
            
            if not frame_paths:
                description = "Error: No frames extracted from video."
            else:
                response = client.chat(
                    model=vl_model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {
                            'role': 'user',
                            'content': prompt + " This is a video clip. Describe the actions in chronological order.",
                            'images': frame_paths,
                        }
                    ],
                    options={"temperature": 0.5, "num_ctx": 16384}
                )
                description = response['message']['content']
                # Cleanup frames
                for p in frame_paths:
                    os.remove(p)
                os.rmdir(temp_frame_dir)