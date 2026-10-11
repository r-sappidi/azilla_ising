function [J, num_spins, known_cut] = rtl_load_dataset(filename)
% Load the exact directed signed-int8 matrix consumed by the RTL testbench.
  fid = fopen(filename, 'r');
  if fid < 0, error('Cannot open dataset %s', filename); end
  cleanup = onCleanup(@() fclose(fid));
  header = fscanf(fid, '%d', 2);
  if numel(header) ~= 2, error('Invalid dataset header'); end
  num_spins = double(header(1));
  known_cut = double(header(2));
  records = fscanf(fid, '%f', [3, Inf]).';
  if isempty(records), J = sparse(num_spins, num_spins); return; end
  src = records(:,1); dst = records(:,2); weight = records(:,3);
  if any(src ~= fix(src)) || any(dst ~= fix(dst)) || ...
     any(src < 1) || any(src > num_spins) || ...
     any(dst < 1) || any(dst > num_spins)
    error('Dataset contains an invalid vertex index');
  end
  if any(weight ~= fix(weight)) || any(weight < -128) || any(weight > 127)
    error('Weights must already be quantized signed-int8 values');
  end
  key = uint64(src-1) * uint64(num_spins) + uint64(dst-1);
  [~, last] = unique(key, 'last');
  J = sparse(src(last), dst(last), double(int8(weight(last))), ...
             num_spins, num_spins);
end
