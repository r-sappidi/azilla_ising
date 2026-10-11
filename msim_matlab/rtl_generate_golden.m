function rtl_generate_golden(dataset_path,output_path,iterations,a,b,noise_amp)
% Write the external golden format consumed by +MATLAB_GOLDEN=<path>.
  if nargin < 3, iterations=1; end
  if nargin < 4, a=0; end
  if nargin < 5, b=1; end
  if nargin < 6, noise_amp=0; end
  [J,n] = rtl_load_dataset(dataset_path);
  state = rtl_initial_state(n); lfsr = rtl_noise_state(n);
  fid = fopen(output_path,'w');
  if fid < 0, error('Cannot create %s',output_path); end
  cleanup = onCleanup(@() fclose(fid));
  fprintf(fid,'%d %d\n',n,iterations);
  for iteration = 1:iterations
    [state,lfsr] = rtl_ising_step(state,J,lfsr,a,b,noise_amp);
    fprintf(fid,'%d\n',state);
  end
  fprintf('Wrote %d iterations of %d spins to %s\n',iterations,n,output_path);
end
