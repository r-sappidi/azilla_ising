%% FUNCTION TO READ IN A RANDOM GRAPH FROM BIQ MAC AND OUTPUT J MATRIX

% Uncomment to test
%[J,N] = findJa('Input_Problems/g05_60.0.txt');

function [J,num_nodes] = findJ(filename)
    fileID = fopen(filename,'r');
    A = fscanf(fileID,'%d');
    B = transpose(reshape(A(3:length(A),:),3,A(2)));

    num_nodes = A(1);

    J = zeros(num_nodes,num_nodes);

    for i = 1:A(2)
        J(B(i,1),B(i,2))=B(i,3);
        J(B(i,2),B(i,1))=B(i,3);
    end
    
    %J = -1*J;
    fclose('all');
end


