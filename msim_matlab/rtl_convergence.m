function history = rtl_convergence(dataset_path, iterations, a, b, noise_amp, csv_path)
% Run the RTL-compatible update and report cut quality and state activity.
  if nargin < 6, csv_path = ''; end
  [J,n,known_cut] = rtl_load_dataset(dataset_path);
  state = rtl_initial_state(n);
  lfsr = rtl_noise_state(n);
  [row,col,weight] = find(J);
  history = zeros(iterations+1,4);
  for iteration = 0:iterations
    cut = 0.5*sum(weight(state(row) ~= state(col)));
    if iteration == 0
      flips = 0;
    else
      flips = sum(state ~= previous_state);
    end
    history(iteration+1,:) = [iteration,cut,cut/known_cut,flips];
    if iteration < iterations
      previous_state = state;
      [state,lfsr] = rtl_ising_step(state,J,lfsr,a,b,noise_amp);
    end
  end
  if ~isempty(csv_path)
    fid = fopen(csv_path,'w');
    if fid < 0, error('Cannot create %s',csv_path); end
    cleanup = onCleanup(@() fclose(fid));
    fprintf(fid,'iteration,cut,known_cut_fraction,spin_flips\n');
    fprintf(fid,'%d,%.0f,%.9f,%d\n',history.');
  end
  fprintf('iteration  cut  fraction  flips\n');
  fprintf('%9d %4.0f %9.4f %6d\n',history.');
end
